"""
The laboratory's own catalogue: what a test is made of, and what normal means.

A laboratory test exists twice in this system, and the difference matters.

``catalogue.Service`` is the **billable** thing — "Full blood count, ₦6,500".
It is what a doctor orders, what the cashier charges for and what appears on
a revenue report. What it does not know is that a full blood count produces
eleven numbers, that one of them is haemoglobin, that haemoglobin is measured
in g/dL, or that 6.8 g/dL in a four-year-old is an emergency.

That is what this module holds. ``LabPanel`` hangs off a service and says how
the laboratory handles it; ``Analyte`` is each measured thing inside it; and
``ReferenceRange`` says what counts as normal, for whom.

**Why age is in days.** Reference ranges for a newborn change by the day —
bilirubin on day two is not bilirubin on day ten. Storing a range as
"0 to 28 days" and "28 days to 16 years" in one unit means there is never a
question of whether "under 1" meant a year or a month, and never a gap
between two bands that a real patient can fall into.

**Why overlapping ranges are refused.** If two ranges both match a
thirty-year-old woman, then "is this result normal" has two answers, and the
one the system picks depends on row order. A laboratory cannot work like
that, so the rule is enforced at the model: within one sex value, the age
windows for an analyte must not overlap. A range for a specific sex is
allowed to overlap one recorded as "any", because that is not ambiguous — it
is the ordinary way a hospital sets a general range and then overrides it for
women. Specificity decides, and ``ranges.range_for`` applies it.

**The catalogue is not patient data.** ``LabPanel``, ``Analyte`` and
``ReferenceRange`` are a hospital's reference data, edited by its laboratory
supervisor and read by everyone. They are scoped to the organisation rather
than the branch for the same reason services are: a full blood count must
mean the same thing at every branch, or group reporting is meaningless.

**The work is.** ``LabRequest`` and ``Sample``, lower down this file, are the
other half: one test asked for on one patient, and one tube with blood in it.
Those are branch-scoped, because a tube is a physical object standing in one
fridge in one building.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BranchScoped, TenantScoped

#: Age bands are stored in days, so these exist to be read, not to be clever.
DAYS_PER_YEAR = 365.25


class Specimen(models.TextChoices):
    """
    What has to be taken from the patient.

    These are the sample types a Nigerian general hospital's laboratory
    actually handles, and they drive the tube the nurse picks up and the
    label that gets printed in milestone 7b.
    """

    WHOLE_BLOOD_EDTA = "blood_edta", _("Whole blood (EDTA)")
    WHOLE_BLOOD_CITRATE = "blood_citrate", _("Whole blood (citrate)")
    WHOLE_BLOOD_FLUORIDE = "blood_fluoride", _("Whole blood (fluoride oxalate)")
    SERUM = "serum", _("Serum (plain)")
    PLASMA_HEPARIN = "plasma_heparin", _("Plasma (heparin)")
    URINE = "urine", _("Urine")
    STOOL = "stool", _("Stool")
    SPUTUM = "sputum", _("Sputum")
    SWAB = "swab", _("Swab")
    CSF = "csf", _("Cerebrospinal fluid")
    OTHER = "other", _("Other")


class ValueType(models.TextChoices):
    """
    How a result is written down.

    A numeric result is compared against a reference range. A coded result is
    one of a short list of words — reactive, non-reactive, not detected — and
    carries its own flag per option, because there is no arithmetic to do on
    "reactive". A text result is a sentence a scientist writes, such as the
    description under a microscope, and is never flagged by the system: the
    judgement is in the words.
    """

    NUMERIC = "numeric", _("A number")
    CODED = "coded", _("One of a list")
    TEXT = "text", _("Free text")


class OptionFlag(models.TextChoices):
    """What a coded answer means. Mirrors the levels in ``ranges.Level``."""

    NORMAL = "normal", _("Normal")
    ABNORMAL = "abnormal", _("Abnormal")
    CRITICAL = "critical", _("Critical — tell the doctor now")


class RangeSex(models.TextChoices):
    """
    Who a reference range applies to.

    ``ANY`` is not "unknown" — it is a deliberate statement that this range
    holds regardless of sex, which is true of most analytes. Patients whose
    sex is not recorded can only ever match an ``ANY`` range, and that is the
    correct behaviour: the system must not guess a patient's sex in order to
    decide whether their haemoglobin is dangerous.
    """

    ANY = "any", _("Any")
    FEMALE = "F", _("Female")
    MALE = "M", _("Male")


class LabPanel(TenantScoped):
    """
    How the laboratory handles one billable test. See the module docstring.

    One per service, because "this service, when it reaches the laboratory,
    is this piece of work". A service with no panel is one the laboratory has
    not set up yet, which the catalogue screen shows as an unfinished row
    rather than hiding.
    """

    service = models.OneToOneField(
        "catalogue.Service",
        on_delete=models.CASCADE,
        related_name="lab_panel",
        help_text=_("The billable test this is the laboratory's side of."),
    )

    specimen = models.CharField(
        max_length=20, choices=Specimen, default=Specimen.SERUM, db_index=True
    )
    #: Spelled out for the person holding the needle: "Purple top (EDTA),
    #: 4 mL". Free text because tube naming is a local habit and a dropdown
    #: of containers would be wrong somewhere within a week.
    container = models.CharField(max_length=120, blank=True)
    volume_ml = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        help_text=_("How much is needed. Leave empty where it does not apply."),
    )
    fasting_required = models.BooleanField(default=False)
    #: What to tell the patient beforehand.
    preparation = models.CharField(max_length=300, blank=True)
    #: What the person collecting and carrying the sample must do.
    handling = models.CharField(max_length=300, blank=True)
    #: Expected time from collection to a verified result. Drives "overdue"
    #: on the laboratory worklist in 7b, and it is a promise to the ward.
    turnaround_minutes = models.PositiveIntegerField(
        default=240, help_text=_("Expected time from sample to verified result.")
    )
    #: Whether the laboratory sends this away rather than doing it here. The
    #: send-out workflow itself arrives in 7c; this flag is what tells the
    #: worklist not to expect a bench result.
    is_send_out = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    #: True for rows seeded from Curavia's own template, so a hospital's own
    #: edits can be told apart from the list it was given.
    from_template = models.BooleanField(default=False)

    class Meta(TenantScoped.Meta):
        verbose_name = _("laboratory panel")
        verbose_name_plural = _("laboratory panels")
        ordering = ("service__name",)
        indexes = [models.Index(fields=["organisation", "is_active"])]

    def __str__(self) -> str:
        return f"{self.code} — {self.name}"

    @property
    def code(self) -> str:
        """The service's code. Not duplicated here, so it cannot drift."""
        return self.service.code

    @property
    def name(self) -> str:
        return self.service.name


class Analyte(TenantScoped):
    """
    One measured thing inside a panel: haemoglobin, sodium, the MP result.

    A panel with a single analyte is normal and not a sign of a modelling
    mistake — a packed cell volume is one number, and an HIV screen is one
    word. Modelling both the one-value and the eleven-value case the same way
    is what lets the result screen, the flagging and the printed report be
    written once.
    """

    panel = models.ForeignKey(LabPanel, on_delete=models.CASCADE, related_name="analytes")

    code = models.CharField(max_length=24, help_text=_("Short code, e.g. HB or WBC."))
    name = models.CharField(max_length=120)
    value_type = models.CharField(max_length=10, choices=ValueType, default=ValueType.NUMERIC)
    #: Printed next to the number and never inferred. An analyte whose unit
    #: is wrong is a result that gets misread.
    unit = models.CharField(max_length=24, blank=True)
    #: How many decimals to show, so 11.0 does not print as 11 on one report
    #: and 11.00 on another.
    decimal_places = models.PositiveSmallIntegerField(default=1)
    #: For a coded analyte: the list of allowed answers and what each means.
    #: Shape: ``[{"label": "Reactive", "flag": "critical"}, ...]``.
    options = models.JSONField(default=list, blank=True)
    #: The LOINC identifier for this measurement, where the hospital knows
    #: it. Blank for almost every row today. It is here because LOINC is the
    #: identifier every outside system — an HMO portal, a national registry,
    #: another hospital's record — asks for, and retro-fitting it onto
    #: results already issued would mean re-coding history.
    loinc_code = models.CharField(max_length=20, blank=True)
    sort_order = models.PositiveSmallIntegerField(
        default=0, help_text=_("Order on the result form and the printed report.")
    )
    is_active = models.BooleanField(default=True, db_index=True)
    from_template = models.BooleanField(default=False)

    class Meta(TenantScoped.Meta):
        verbose_name = _("analyte")
        ordering = ("panel__service__name", "sort_order", "name")
        constraints = [
            models.UniqueConstraint(fields=["panel", "code"], name="unique_analyte_code_per_panel")
        ]
        indexes = [models.Index(fields=["organisation", "panel", "is_active"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"

    @property
    def is_numeric(self) -> bool:
        return self.value_type == ValueType.NUMERIC

    def option_labels(self) -> list[str]:
        return [str(option.get("label", "")) for option in self.options]

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}

        if self.value_type == ValueType.CODED:
            if not self.options:
                errors["options"] = _("A coded analyte needs at least one allowed answer.")
            else:
                errors.update(self._option_errors())
            if self.unit:
                errors["unit"] = _("A coded answer has no unit.")
        elif self.options:
            errors["options"] = _("Only a coded analyte has a list of answers.")

        if self.value_type != ValueType.NUMERIC and self.decimal_places:
            # Not an error worth refusing a save over, but it must not be
            # left looking meaningful on the screen.
            self.decimal_places = 0

        if errors:
            raise ValidationError(errors)

    def _option_errors(self) -> dict[str, Any]:
        """Check the shape of ``options``, which is JSON and so unenforced."""
        allowed = set(OptionFlag.values)
        labels: list[str] = []
        for index, option in enumerate(self.options, start=1):
            if not isinstance(option, dict):
                return {"options": _("Answer %(n)d is not filled in properly.") % {"n": index}}
            label = str(option.get("label", "")).strip()
            if not label:
                return {"options": _("Answer %(n)d has no wording.") % {"n": index}}
            if option.get("flag") not in allowed:
                return {
                    "options": _("Answer “%(label)s” must be marked normal, abnormal or critical.")
                    % {"label": label}
                }
            labels.append(label.casefold())
        if len(set(labels)) != len(labels):
            return {"options": _("Two answers have the same wording.")}
        return {}


class ReferenceRange(TenantScoped):
    """
    What normal means for one analyte, for one sex and one age band.

    ``low`` and ``high`` are the normal band, inclusive: a result equal to
    ``low`` is normal, not low. One side may be empty, because one-sided
    ranges are real — a platelet count has a lower limit that matters and an
    upper one that mostly does not.

    ``critical_low`` and ``critical_high`` are the thresholds at which
    somebody is telephoned. They are checked inclusively from the outside: a
    result at or below ``critical_low`` is critical. In 7c this is what
    raises the alert to the doctor who ordered the test.
    """

    analyte = models.ForeignKey(Analyte, on_delete=models.CASCADE, related_name="ranges")

    sex = models.CharField(max_length=5, choices=RangeSex, default=RangeSex.ANY, db_index=True)
    age_min_days = models.PositiveIntegerField(
        default=0, help_text=_("From this age, in days. 0 means from birth.")
    )
    age_max_days = models.PositiveIntegerField(
        null=True, blank=True, help_text=_("Up to and including this age. Empty means no limit.")
    )

    low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    critical_low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    critical_high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    #: Shown beside the range on the report: "fasting", "in pregnancy".
    note = models.CharField(max_length=120, blank=True)
    from_template = models.BooleanField(default=False)

    class Meta(TenantScoped.Meta):
        verbose_name = _("reference range")
        ordering = ("analyte__sort_order", "age_min_days", "sex")
        indexes = [models.Index(fields=["organisation", "analyte", "sex"])]

    def __str__(self) -> str:
        return f"{self.analyte.code} {self.band_label()}: {self.normal_label()}"

    # -- reading it back ----------------------------------------------------

    def covers(self, age_days: int | None) -> bool:
        """
        Whether this range applies at that age.

        An unknown age matches only a range with no age limits at all. The
        alternative — treating unknown as "probably an adult" — would flag a
        baby's result against an adult's range, which is the one mistake this
        whole module exists to prevent.
        """
        if age_days is None:
            return self.age_min_days == 0 and self.age_max_days is None
        if age_days < self.age_min_days:
            return False
        return self.age_max_days is None or age_days <= self.age_max_days

    def band_label(self) -> str:
        """ "Women, 28 days to 16 years" — the heading on the printed report."""
        who = "" if self.sex == RangeSex.ANY else str(self.get_sex_display())
        age = _age_band_label(self.age_min_days, self.age_max_days)
        parts = [part for part in (who, age) if part]
        return ", ".join(parts) if parts else "Everyone"

    def critical_label(self) -> str:
        """ "below 7 or above 20" — what prints beside the telephone warning."""
        parts = []
        if self.critical_low is not None:
            parts.append(f"below {_trim(self.critical_low)}")
        if self.critical_high is not None:
            parts.append(f"above {_trim(self.critical_high)}")
        return " or ".join(parts)

    def normal_label(self) -> str:
        """The band as a laboratory would print it: "11.5 - 16.5"."""
        if self.low is not None and self.high is not None:
            return f"{_trim(self.low)} - {_trim(self.high)}"
        if self.low is not None:
            return f"≥ {_trim(self.low)}"
        if self.high is not None:
            return f"≤ {_trim(self.high)}"
        return ""

    # -- validation ---------------------------------------------------------

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}

        if self.analyte_id and not self.analyte.is_numeric:
            raise ValidationError(
                _(
                    "Only a numeric analyte has a reference range. A coded answer "
                    "carries its own flag on each option."
                )
            )

        if self.age_max_days is not None and self.age_max_days < self.age_min_days:
            errors["age_max_days"] = _("The upper age is below the lower one.")

        if self.low is not None and self.high is not None and self.low > self.high:
            errors["high"] = _("The top of the normal band is below the bottom of it.")
        if self.critical_low is not None and self.low is not None and self.critical_low > self.low:
            errors["critical_low"] = _("The critical level is inside the normal band.")
        if (
            self.critical_high is not None
            and self.high is not None
            and self.critical_high < self.high
        ):
            errors["critical_high"] = _("The critical level is inside the normal band.")
        if (
            self.critical_low is not None
            and self.critical_high is not None
            and self.critical_low >= self.critical_high
        ):
            errors["critical_high"] = _("The two critical levels cross over.")

        if all(
            value is None for value in (self.low, self.high, self.critical_low, self.critical_high)
        ):
            errors["low"] = _("Set at least one limit, or the range says nothing.")

        if errors:
            raise ValidationError(errors)

        clash = self._overlapping()
        if clash is not None:
            raise ValidationError(
                _(
                    "This overlaps “%(existing)s”, which already covers part of that "
                    "age range for the same sex. Two ranges that both apply would give "
                    "one result two answers."
                )
                % {"existing": clash.band_label()}
            )

    def _overlapping(self) -> ReferenceRange | None:
        """
        The first range for this analyte and sex whose age band overlaps.

        Only the same ``sex`` value is checked. A female range overlapping an
        "any" range is allowed and resolved by specificity — see the module
        docstring and ``ranges.range_for``.
        """
        if not self.analyte_id:
            return None
        others = ReferenceRange.objects.filter(analyte_id=self.analyte_id, sex=self.sex)
        if self.pk:
            others = others.exclude(pk=self.pk)

        mine_end = self.age_max_days
        for other in others:
            starts_after_mine = mine_end is not None and other.age_min_days > mine_end
            ends_before_mine = (
                other.age_max_days is not None and other.age_max_days < self.age_min_days
            )
            if not starts_after_mine and not ends_before_mine:
                return other
        return None


# --------------------------------------------------------------------------
# Small helpers used by the labels above
# --------------------------------------------------------------------------


def _trim(value: Decimal) -> str:
    """Print 11.5 as "11.5" and 11.0000 as "11", the way a report would."""
    trimmed = value.normalize()
    if trimmed == trimmed.to_integral_value():
        trimmed = trimmed.to_integral_value()
    return f"{trimmed:f}"


def _age_band_label(min_days: int, max_days: int | None) -> str:
    if min_days == 0 and max_days is None:
        return ""
    if min_days == 0:
        return f"up to {_age_label(max_days)}"
    if max_days is None:
        return f"{_age_label(min_days)} and over"
    return f"{_age_label(min_days)} to {_age_label(max_days)}"


def _age_label(days: int | None) -> str:
    """Days, months or years — whichever a person would actually say."""
    if days is None:
        return ""
    if days < 31:
        return f"{days} day{'s' if days != 1 else ''}"
    if days < 365:
        months = round(days / 30.4375)
        return f"{months} month{'s' if months != 1 else ''}"
    years = days / DAYS_PER_YEAR
    # round, not int: 365 days is one year, and truncating 11.993 gave
    # "11 years" for a band that starts on a twelfth birthday.
    whole = round(years) if abs(years - round(years)) < 0.02 else round(years, 1)
    return f"{whole:g} year{'s' if whole != 1 else ''}"


# --------------------------------------------------------------------------
# The work: what was asked for, and what was taken
# --------------------------------------------------------------------------


class AccessionCounter(models.Model):
    """
    The next accession number, per hospital per year.

    The same shape as ``patients.MedicalRecordCounter`` and for the same
    reasons: a row with a lock rather than a database sequence, so the
    numbering restarts each January and so one hospital's count is not
    inferable from another's.

    Per **organisation**, not per branch. A tube collected at Ikeja can be
    carried to the laboratory at Lekki, and two tubes in one rack must never
    share a number whatever building they were drawn in.
    """

    organisation = models.ForeignKey(
        "core.Organisation", on_delete=models.CASCADE, related_name="accession_counters"
    )
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("accession counter")
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "year"],
                name="one_accession_counter_per_org_per_year",
            )
        ]

    def __str__(self) -> str:
        return f"{self.organisation_id} {self.year}: {self.last_number}"


def next_accession_number(organisation_id, *, year: int | None = None) -> str:
    """
    Issue the next accession number: ``L26-000147``.

    Short on purpose. This is printed as a Code 128 barcode on a sticker
    25 mm tall, and it is also the thing a scientist reads aloud down the
    phone to a ward. ``L``, two digits of the year, six of sequence: eleven
    characters, which fits across a 50 mm label at a width a cheap handheld
    scanner reads first time.

    Takes a row lock, like an MRN does. Two nurses drawing blood in different
    rooms at the same second must not be given the same number: everything
    downstream - the label, the bench worksheet, the result - is keyed on it.
    """
    year = year or timezone.localdate().year
    with transaction.atomic():
        counter, _created = AccessionCounter.objects.select_for_update().get_or_create(
            organisation_id=organisation_id, year=year
        )
        counter.last_number += 1
        counter.save(update_fields=["last_number"])
        return f"L{year % 100:02d}-{counter.last_number:06d}"


class SampleStatus(models.TextChoices):
    """
    Where a tube is.

    Three states, because a tube has only three honest ones: it has been
    drawn, it has been booked in at the laboratory, or it was refused. What
    happens on the bench after booking in belongs to the request, not to the
    glass.
    """

    COLLECTED = "collected", _("Collected")
    RECEIVED = "received", _("Received at the laboratory")
    REJECTED = "rejected", _("Rejected")


class RejectionReason(models.TextChoices):
    """
    Why a sample was refused.

    A fixed list, and required. "Sample rejected" with no reason is the most
    common cause of an argument between a ward and a laboratory, and it also
    hides the thing the hospital most needs to see: that one ward's samples
    keep clotting because the blood is being drawn through too fine a needle.
    These are the reasons that appear on a real rejection log.
    """

    HAEMOLYSED = "haemolysed", _("Haemolysed")
    CLOTTED = "clotted", _("Clotted")
    INSUFFICIENT = "insufficient", _("Not enough sample")
    WRONG_CONTAINER = "wrong_container", _("Wrong container")
    UNLABELLED = "unlabelled", _("No label")
    MISLABELLED = "mislabelled", _("Label does not match the request")
    LEAKED = "leaked", _("Leaked or broken in transit")
    TOO_OLD = "too_old", _("Too long since collection")
    CONTAMINATED = "contaminated", _("Contaminated")
    WRONG_PATIENT = "wrong_patient", _("Taken from the wrong patient")
    OTHER = "other", _("Something else")


#: Rejections that mean somebody has to be told now rather than at the end of
#: the shift. A tube from the wrong patient is a patient-safety incident, not
#: a redraw, and a mislabelled one is the same mistake caught a step earlier.
SERIOUS_REJECTIONS = (RejectionReason.WRONG_PATIENT, RejectionReason.MISLABELLED)


class Sample(BranchScoped):
    """
    One physical tube, pot or swab.

    The row is created when the sample is **drawn**, not when the test is
    ordered. A tube that does not exist yet has no accession number, because
    the number is what is printed on the label, and a label printed before
    the blood is taken is a label that ends up on the wrong tube. Curavia
    follows the bedside rule: draw, then label, in front of the patient.

    One tube can carry several tests. A full blood count and a packed cell
    volume are both EDTA, so one purple tube serves both, and
    ``service.plan_collection`` is what works that out from the specimen on
    each panel.

    ``panel_names`` is a snapshot. When a tube is rejected its requests move
    on to a fresh tube, so without the snapshot a rejection log would show a
    rejected sample that appears to have been for nothing.
    """

    accession = models.CharField(
        max_length=20,
        db_index=True,
        help_text=_("The number on the label and in the barcode."),
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="lab_samples"
    )

    specimen = models.CharField(max_length=20, choices=Specimen, db_index=True)
    #: What the tube is, in the words the catalogue used at the time: "Purple
    #: top (EDTA), 4 mL". Snapshotted so a label reprinted next year still
    #: describes the tube that was actually drawn.
    container = models.CharField(max_length=120, blank=True)
    #: "FBC, PCV" - see the class docstring.
    panel_names = models.CharField(max_length=300, blank=True)

    status = models.CharField(
        max_length=12, choices=SampleStatus, default=SampleStatus.COLLECTED, db_index=True
    )

    collected_at = models.DateTimeField(db_index=True)
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    collection_note = models.CharField(
        max_length=300, blank=True, help_text=_("Anything the laboratory should know.")
    )

    received_at = models.DateTimeField(null=True, blank=True, db_index=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    rejection_reason = models.CharField(max_length=20, choices=RejectionReason, blank=True)
    rejection_note = models.CharField(max_length=300, blank=True)

    #: The tube this one replaces. Set on the redraw, so the chain reads
    #: forwards from the rejection and a third attempt is visible as a third
    #: attempt.
    recollection_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recollections",
    )

    #: When a label was last produced. A reprint is ordinary - labels tear,
    #: printers jam - but a tube whose label has been printed nine times is
    #: worth a supervisor's attention.
    label_printed_at = models.DateTimeField(null=True, blank=True)
    label_print_count = models.PositiveSmallIntegerField(default=0)

    class Meta(BranchScoped.Meta):
        verbose_name = _("sample")
        ordering = ("-collected_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "accession"], name="unique_accession_per_organisation"
            )
        ]
        indexes = [
            models.Index(fields=["organisation", "branch", "status"]),
            models.Index(fields=["organisation", "patient", "-collected_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.accession} ({self.get_specimen_display()})"

    @property
    def is_rejected(self) -> bool:
        return self.status == SampleStatus.REJECTED

    @property
    def is_serious_rejection(self) -> bool:
        """Whether this rejection is a patient-safety matter. See the list."""
        return self.is_rejected and self.rejection_reason in SERIOUS_REJECTIONS

    def rejection_label(self) -> str:
        """Clotted, and the sentence beside it, for the log and the ward."""
        if not self.is_rejected:
            return ""
        reason = str(self.get_rejection_reason_display())
        return f"{reason} - {self.rejection_note}" if self.rejection_note else reason

    def minutes_in_transit(self) -> int | None:
        """How long between the needle and the bench. Empty until received."""
        if self.received_at is None:
            return None
        return int((self.received_at - self.collected_at).total_seconds() // 60)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, Any] = {}
        if self.status == SampleStatus.REJECTED and not self.rejection_reason:
            errors["rejection_reason"] = _("Say why the sample was rejected.")
        if self.rejection_reason == RejectionReason.OTHER and not self.rejection_note.strip():
            errors["rejection_note"] = _("Something else needs a sentence saying what.")
        if self.recollection_of_id and self.recollection_of_id == self.pk:
            errors["recollection_of"] = _("A sample cannot replace itself.")
        if errors:
            raise ValidationError(errors)


class RequestStatus(models.TextChoices):
    """
    Where one test has got to.

    Longer than ``SampleStatus`` because this is the thing a ward rings up
    about. ``AWAITING_COLLECTION`` is where a test sits both before its first
    tube and after a tube was rejected: from the nurse's point of view those
    are the same job, and giving them two names would mean two queues to
    watch. The difference is still visible - the request counts its
    rejections, and the worklist says so.

    Results, verification and release are added in 7c.
    """

    AWAITING_COLLECTION = "awaiting", _("Waiting for the sample")
    COLLECTED = "collected", _("Sample taken")
    RECEIVED = "received", _("At the laboratory")
    IN_PROGRESS = "in_progress", _("Being done")
    CANCELLED = "cancelled", _("Cancelled")


#: Statuses in which a test is still somebody's live job.
OPEN_REQUEST_STATUSES = (
    RequestStatus.AWAITING_COLLECTION,
    RequestStatus.COLLECTED,
    RequestStatus.RECEIVED,
    RequestStatus.IN_PROGRESS,
)


class LabRequest(BranchScoped):
    """
    One laboratory test asked for on one patient.

    Created automatically when the doctor signs the order basket, so the
    laboratory sees work arrive without anybody re-typing it. One per
    laboratory order, which is why ``order`` is a one-to-one: the order is
    what the doctor sees and what the cashier charges for, and this is the
    same thing seen from the bench.

    ``specimen`` and ``container`` are copied from the panel at the moment of
    ordering rather than read through it. A supervisor correcting the
    catalogue on Tuesday must not silently change what tube Monday's
    outstanding request is waiting for.
    """

    order = models.OneToOneField(
        "orders.Order", on_delete=models.CASCADE, related_name="lab_request"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="lab_requests"
    )
    encounter = models.ForeignKey(
        "journey.Encounter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lab_requests",
    )
    panel = models.ForeignKey(LabPanel, on_delete=models.PROTECT, related_name="requests")

    #: Snapshots. See the class docstring.
    specimen = models.CharField(max_length=20, choices=Specimen, db_index=True)
    container = models.CharField(max_length=120, blank=True)
    priority = models.CharField(max_length=10, db_index=True)

    status = models.CharField(
        max_length=12,
        choices=RequestStatus,
        default=RequestStatus.AWAITING_COLLECTION,
        db_index=True,
    )
    sample = models.ForeignKey(
        Sample,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests",
        help_text=_("The tube this is currently on. Empty while it waits for one."),
    )

    requested_at = models.DateTimeField(db_index=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    #: When the ward was promised a result: the moment of ordering plus the
    #: panel's turnaround. Stored rather than computed, because the promise
    #: was made against the turnaround as it stood that day.
    due_at = models.DateTimeField(db_index=True)

    started_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)

    #: How many tubes have been rejected for this test. Read off the worklist
    #: so a nurse drawing the third one knows it is the third one.
    rejection_count = models.PositiveSmallIntegerField(default=0)

    class Meta(BranchScoped.Meta):
        verbose_name = _("laboratory request")
        ordering = ("due_at",)
        indexes = [
            models.Index(fields=["organisation", "branch", "status"]),
            models.Index(fields=["organisation", "branch", "status", "due_at"]),
            models.Index(fields=["organisation", "patient", "-requested_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.panel.code} for {self.patient_id}"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_REQUEST_STATUSES

    @property
    def needs_collection(self) -> bool:
        return self.status == RequestStatus.AWAITING_COLLECTION

    def is_overdue(self, *, now=None) -> bool:
        """
        Past the time the ward was promised, and not finished.

        A cancelled request is never overdue, and neither is one the
        laboratory has already finished with - being late is only meaningful
        while somebody is still waiting.
        """
        if not self.is_open:
            return False
        return (now or timezone.now()) > self.due_at

    def minutes_late(self, *, now=None) -> int:
        """Zero when it is not late, so the screen can print it unguarded."""
        if not self.is_overdue(now=now):
            return 0
        return int(((now or timezone.now()) - self.due_at).total_seconds() // 60)
