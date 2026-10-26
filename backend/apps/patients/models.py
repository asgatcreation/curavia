"""
The patient record.

**A patient belongs to the Organisation, not to a Branch.** That is the
decision the whole tenancy design was built around: a group's branches share
patients while keeping staff, stock, prices and queues separate, and a person
registered at Ikeja who turns up at Lekki is the same person with the same
MRN. `home_branch` is a convenience — where they usually go — never a
boundary.

Three things here are shaped by how a Nigerian front desk actually works:

* **Names are three fields, not two.** Many patients give a first name, a
  surname and one or more other names, and the other names are frequently
  what a relative will use at the desk. Searching has to cover all three.

* **Nothing is required except a name and a sex.** A date of birth is often
  approximate and sometimes unknown; a phone number may belong to a
  neighbour. A registration form that refuses to save without them produces
  invented data, which is worse than missing data because nobody can tell
  which is which. `date_of_birth_is_estimated` records the difference.

* **"No known allergies" is not the same as "nobody has asked".** The
  patient banner shows those two differently, so the record has to
  distinguish them: `allergies_checked_at` is null until somebody takes a
  history, and an empty allergy list after that means none.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import PHONE_VALIDATOR, TenantScoped

#: Nigerian phone numbers, loosely. Deliberately permissive: a record is more
#: useful with a slightly odd number in it than without the number.
NIN_VALIDATOR = RegexValidator(r"^\d{11}$", _("A National Identification Number is 11 digits."))


class Sex(models.TextChoices):
    """
    Sex as recorded for clinical purposes.

    Two values plus "unknown", because this field drives reference ranges,
    NEWS2 scoring and obstetric screening — it is a clinical observation,
    not a question about identity. Gender identity, where a hospital records
    it at all, is a separate field and is not this one.
    """

    FEMALE = "F", _("Female")
    MALE = "M", _("Male")
    UNKNOWN = "U", _("Unknown")


class BloodGroup(models.TextChoices):
    A_POS = "A+", "A+"
    A_NEG = "A-", "A-"
    B_POS = "B+", "B+"
    B_NEG = "B-", "B-"
    AB_POS = "AB+", "AB+"
    AB_NEG = "AB-", "AB-"
    O_POS = "O+", "O+"
    O_NEG = "O-", "O-"


class Genotype(models.TextChoices):
    """
    Haemoglobin genotype.

    Asked at registration in Nigeria far more than elsewhere, because sickle
    cell disease is common and the answer changes what a doctor does in an
    emergency. AS and AC are carriers; SS, SC and CC are the disease.
    """

    AA = "AA", "AA"
    AS = "AS", "AS"
    AC = "AC", "AC"
    SS = "SS", "SS"
    SC = "SC", "SC"
    CC = "CC", "CC"


class MaritalStatus(models.TextChoices):
    SINGLE = "single", _("Single")
    MARRIED = "married", _("Married")
    DIVORCED = "divorced", _("Divorced")
    WIDOWED = "widowed", _("Widowed")
    SEPARATED = "separated", _("Separated")


class MedicalRecordCounter(models.Model):
    """
    The next medical record number, per hospital per year.

    A row with a lock rather than a PostgreSQL sequence, for two reasons. A
    sequence is global to the database and would leak how many patients every
    other hospital has registered — the number is visible on a patient's card.
    And a sequence cannot be reset per year, which the MRN format requires.

    Not ``TenantScoped``: it is written during registration while the scope is
    already set, and it holds no patient data. Scoped by an explicit
    organisation column and a unique constraint instead.
    """

    organisation = models.ForeignKey(
        "core.Organisation", on_delete=models.CASCADE, related_name="mrn_counters"
    )
    year = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("medical record counter")
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "year"], name="one_mrn_counter_per_org_per_year"
            )
        ]

    def __str__(self) -> str:
        return f"{self.organisation_id} {self.year}: {self.last_number}"


def next_medical_record_number(
    organisation_id, branch_code: str, *, year: int | None = None
) -> str:
    """
    Issue the next MRN: ``IKJ/2026/000123``.

    The branch code is the branch the patient registered at, the year is the
    year they registered, and the sequence restarts each January — which is
    how paper record rooms have always numbered files, and what the staff
    using this will expect to see written on a folder.

    Takes a row lock. Two clerks registering at the same moment must not be
    issued the same number: an MRN is written on a physical card and spoken
    aloud, so a collision is not a database problem but a wrong-patient one.
    """
    year = year or timezone.localdate().year
    with transaction.atomic():
        counter, _created = MedicalRecordCounter.objects.select_for_update().get_or_create(
            organisation_id=organisation_id, year=year
        )
        counter.last_number += 1
        counter.save(update_fields=["last_number"])
        return f"{branch_code.upper()}/{year}/{counter.last_number:06d}"


class Patient(TenantScoped):
    """One person, known to one hospital."""

    mrn = models.CharField(max_length=40, db_index=True, verbose_name=_("medical record number"))

    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    other_names = models.CharField(
        max_length=120, blank=True, help_text=_("Middle or other names, as given.")
    )

    sex = models.CharField(max_length=1, choices=Sex, default=Sex.UNKNOWN, db_index=True)
    date_of_birth = models.DateField(null=True, blank=True)
    date_of_birth_is_estimated = models.BooleanField(
        default=False,
        help_text=_("Set when the patient gave an age rather than a date."),
    )

    phone = models.CharField(max_length=24, blank=True, db_index=True, validators=[PHONE_VALIDATOR])
    alternate_phone = models.CharField(max_length=24, blank=True, validators=[PHONE_VALIDATOR])
    email = models.EmailField(blank=True)

    address = models.TextField(blank=True)
    state = models.CharField(max_length=60, blank=True)
    lga = models.CharField(max_length=80, blank=True, verbose_name=_("local government area"))

    blood_group = models.CharField(max_length=3, choices=BloodGroup, blank=True)
    genotype = models.CharField(max_length=2, choices=Genotype, blank=True)
    marital_status = models.CharField(max_length=16, choices=MaritalStatus, blank=True)
    occupation = models.CharField(max_length=120, blank=True)

    home_branch = models.ForeignKey(
        "core.Branch",
        on_delete=models.PROTECT,
        related_name="patients",
        null=True,
        blank=True,
        help_text=_("Where they usually attend. Not a restriction."),
    )
    default_payer = models.ForeignKey(
        "catalogue.Payer",
        on_delete=models.PROTECT,
        related_name="patients",
        null=True,
        blank=True,
    )

    #: Null until somebody takes an allergy history. An empty allergy list
    #: with this set means "no known allergies"; with it null it means
    #: "nobody has asked", and the patient banner shows those differently.
    allergies_checked_at = models.DateTimeField(null=True, blank=True)

    #: Registration under a false or unknown identity, to be merged later.
    #: Emergency quick-registration sets it.
    is_provisional = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Registered in an emergency without confirmed identity."),
    )
    #: Set when this record was merged INTO another. Kept, never deleted, so
    #: an old MRN written on a card still resolves to the surviving record.
    merged_into = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="merged_from"
    )

    is_deceased = models.BooleanField(default=False)
    died_on = models.DateField(null=True, blank=True)

    registered_at = models.DateTimeField(default=timezone.now)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patients_registered",
    )
    notes = models.TextField(blank=True, help_text=_("Administrative notes. Not clinical."))

    #: Maintained by a database trigger — see the migration. A Python-side
    #: update would go stale on any bulk write, and the search is how the
    #: front desk finds anybody.
    search_vector = SearchVectorField(null=True, editable=False)
    search_text = models.TextField(
        blank=True,
        editable=False,
        help_text=_("Flattened name, phone and MRN, for trigram similarity."),
    )

    class Meta(TenantScoped.Meta):
        verbose_name = _("patient")
        ordering = ("last_name", "first_name")
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "mrn"], name="unique_mrn_per_organisation"
            )
        ]
        indexes = [
            # Full-text ranking.
            GinIndex(fields=["search_vector"], name="patient_search_vector_gin"),
            # Fuzzy matching: "Adebayou" finds "Adebayo", and a phone
            # fragment finds the number. Created in the migration with the
            # trigram operator class, which Django cannot express here.
            models.Index(fields=["organisation", "is_provisional"], name="patient_org_prov_idx"),
            models.Index(fields=["organisation", "last_name"], name="patient_org_surname_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.mrn})"

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.other_names, self.last_name]
        return " ".join(part for part in parts if part).strip()

    @property
    def display_name(self) -> str:
        """Name as a clerk would read it off a list: surname first."""
        return f"{self.last_name}, {self.first_name}".strip(", ")

    @property
    def allergies_known(self) -> bool:
        """True once somebody has actually asked. See the class docstring."""
        return self.allergies_checked_at is not None

    @property
    def is_merged(self) -> bool:
        return self.merged_into_id is not None

    def clean(self) -> None:
        super().clean()
        if not self.first_name.strip() or not self.last_name.strip():
            raise ValidationError({"last_name": _("A patient needs a first name and a surname.")})
        if self.date_of_birth and self.date_of_birth > timezone.localdate():
            raise ValidationError({"date_of_birth": _("That date is in the future.")})
        if self.died_on and not self.is_deceased:
            raise ValidationError(
                {"is_deceased": _("A date of death needs the deceased flag set.")}
            )
        if self.merged_into_id == self.pk and self.pk:
            raise ValidationError({"merged_into": _("A record cannot be merged into itself.")})


class PatientIdentifier(TenantScoped):
    """
    A typed external identifier.

    One table rather than a column per kind, because the list grows: NIN, a
    BVN, an HMO enrolee number, a passport, a company staff number, a school
    number. A column per type means a migration every time a hospital signs a
    new corporate client.
    """

    class Kind(models.TextChoices):
        NIN = "nin", _("National Identification Number")
        BVN = "bvn", _("Bank Verification Number")
        PASSPORT = "passport", _("International passport")
        DRIVERS_LICENCE = "drivers_licence", _("Driver's licence")
        HMO = "hmo", _("HMO enrolee number")
        NHIA = "nhia", _("NHIA number")
        CORPORATE = "corporate", _("Corporate or staff number")
        OTHER = "other", _("Other")

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="identifiers")
    kind = models.CharField(max_length=24, choices=Kind, db_index=True)
    value = models.CharField(max_length=80, db_index=True)
    #: Which scheme issued it, for HMO and corporate numbers.
    payer = models.ForeignKey(
        "catalogue.Payer",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="patient_identifiers",
    )
    is_verified = models.BooleanField(
        default=False, help_text=_("Checked against the card or the scheme.")
    )
    note = models.CharField(max_length=160, blank=True)

    class Meta(TenantScoped.Meta):
        verbose_name = _("patient identifier")
        ordering = ("kind", "value")
        constraints = [
            # The same NIN twice for one patient is a data-entry slip. The
            # same NIN on two patients is a duplicate record, which is a
            # different problem and is caught at registration instead — not
            # here, because sometimes it is genuinely two people and a typo.
            models.UniqueConstraint(
                fields=["patient", "kind", "value"],
                name="unique_identifier_per_patient",
            )
        ]
        indexes = [models.Index(fields=["organisation", "kind", "value"])]

    def __str__(self) -> str:
        return f"{self.get_kind_display()}: {self.value}"

    def clean(self) -> None:
        super().clean()
        if self.kind == self.Kind.NIN and self.value:
            NIN_VALIDATOR(self.value)


class NextOfKin(TenantScoped):
    """Who to call. Often the only reliable phone number on the record."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="next_of_kin")
    name = models.CharField(max_length=160)
    relationship = models.CharField(max_length=60, help_text=_("Spouse, son, neighbour…"))
    phone = models.CharField(max_length=24, validators=[PHONE_VALIDATOR])
    alternate_phone = models.CharField(max_length=24, blank=True, validators=[PHONE_VALIDATOR])
    address = models.TextField(blank=True)
    is_primary = models.BooleanField(default=True)

    class Meta(TenantScoped.Meta):
        verbose_name = _("next of kin")
        verbose_name_plural = _("next of kin")
        ordering = ("-is_primary", "name")

    def __str__(self) -> str:
        return f"{self.name} ({self.relationship})"


class Allergy(TenantScoped):
    """
    A recorded allergy.

    This is the table the pharmacy check reads, so it is deliberately plain:
    a substance as text, what happened, and how bad. Coding substances
    against a drug dictionary arrives with the pharmacy module; until then a
    pharmacist reading "Penicillin — rash" is better served than by a record
    that refused to be saved because the substance was not in a list.
    """

    class Severity(models.TextChoices):
        MILD = "mild", _("Mild")
        MODERATE = "moderate", _("Moderate")
        SEVERE = "severe", _("Severe")
        ANAPHYLAXIS = "anaphylaxis", _("Anaphylaxis")

    class Kind(models.TextChoices):
        DRUG = "drug", _("Medicine")
        FOOD = "food", _("Food")
        ENVIRONMENT = "environment", _("Environmental")
        OTHER = "other", _("Other")

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="allergies")
    substance = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind, default=Kind.DRUG)
    reaction = models.CharField(max_length=200, blank=True, help_text=_("Rash, swelling, …"))
    severity = models.CharField(max_length=16, choices=Severity, default=Severity.MODERATE)
    noted_on = models.DateField(default=timezone.localdate)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    is_active = models.BooleanField(
        default=True, help_text=_("Cleared only when a clinician rules it out.")
    )

    class Meta(TenantScoped.Meta):
        verbose_name = _("allergy")
        verbose_name_plural = _("allergies")
        ordering = ("-severity", "substance")
        constraints = [
            models.UniqueConstraint(
                fields=["patient", "substance"], name="unique_allergy_per_patient"
            )
        ]

    def __str__(self) -> str:
        return f"{self.substance} ({self.get_severity_display()})"

    @property
    def is_critical(self) -> bool:
        return self.severity in {self.Severity.SEVERE, self.Severity.ANAPHYLAXIS}
