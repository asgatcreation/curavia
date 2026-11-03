"""
The clinical record: observations, triage decisions and nursing notes.

One app rather than one per module. The permission codes and the module
switches stay separate — ``triage.vitals.record`` and ``clinical.note.read``
are gated independently, and a clinic can run triage without the EMR — but
the tables belong together because they all hang off the same encounter and
are read together on the same screen. Splitting them would buy nothing except
two migration histories to keep in step.

**Observations are append-only.** A set of vitals is a measurement taken at a
moment, not a property of the patient, and "the current observations" is
simply the most recent row. The alternative — one mutable row per patient,
overwritten — destroys the thing the whole feature is for, which is seeing
that a pulse of 96 was 72 two hours ago.

Nothing here is ever edited. A reading entered wrongly is **corrected by
superseding it**, with the wrong one kept and marked. A nurse who typed 220
instead of 120 and then quietly changed it leaves no trace that anybody
reacted to a hypertensive crisis that never happened.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.clinical import news2
from apps.core.models import BranchScoped


class TriageCategory(models.TextChoices):
    """
    Five levels, the Manchester colours.

    The same scale the design system draws (frontend/shared/src/ui/status.ts)
    and the one Nigerian private hospitals running formal triage generally
    adopt. ``target_minutes`` below is the promise each colour makes.
    """

    RED = "red", _("Red — immediate")
    ORANGE = "orange", _("Orange — very urgent")
    YELLOW = "yellow", _("Yellow — urgent")
    GREEN = "green", _("Green — routine")
    BLUE = "blue", _("Blue — non-urgent")


#: How long each colour promises. Used to flag a patient who has waited past
#: their own target, which is the only wait that means anything clinically —
#: a green patient at 90 minutes is fine and a red one at 5 is a crisis.
TRIAGE_TARGET_MINUTES: dict[str, int | None] = {
    TriageCategory.RED: 0,
    TriageCategory.ORANGE: 10,
    TriageCategory.YELLOW: 60,
    TriageCategory.GREEN: 240,
    TriageCategory.BLUE: None,
}


class Observation(BranchScoped):
    """
    One set of vitals, taken at one moment.

    Every field is optional. A triage nurse with a queue of forty people
    takes a pulse and a temperature and moves on; a form that refuses to save
    without a respiratory rate produces an invented respiratory rate, which
    is worse than a blank because it will be scored.

    The NEWS2 score is **stored as well as computable**. Two reasons: the
    score that was in front of the nurse at the time is a fact about what they
    were told, and recomputing it later from a changed algorithm would
    rewrite history. ``score_version`` records which revision produced it.
    """

    class Position(models.TextChoices):
        SITTING = "sitting", _("Sitting")
        LYING = "lying", _("Lying")
        STANDING = "standing", _("Standing")

    encounter = models.ForeignKey(
        "journey.Encounter", on_delete=models.PROTECT, related_name="observations"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="observations"
    )

    taken_at = models.DateTimeField(default=timezone.now, db_index=True)
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    # -- the seven NEWS2 parameters -----------------------------------------
    respiration_rate = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(2), MaxValueValidator(80)]
    )
    spo2 = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("oxygen saturation"),
        validators=[MinValueValidator(40), MaxValueValidator(100)],
    )
    on_oxygen = models.BooleanField(
        null=True, blank=True, help_text=_("On supplemental oxygen at the time.")
    )
    oxygen_litres = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True, verbose_name=_("oxygen, litres/min")
    )
    systolic_bp = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(40), MaxValueValidator(300)]
    )
    diastolic_bp = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(20), MaxValueValidator(200)]
    )
    pulse = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(20), MaxValueValidator(250)]
    )
    temperature = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        verbose_name=_("temperature, °C"),
        validators=[MinValueValidator(Decimal(25)), MaxValueValidator(Decimal(45))],
    )
    consciousness = models.CharField(
        max_length=1,
        blank=True,
        choices=[
            (member.value, member.name.replace("_", " ").title()) for member in news2.Consciousness
        ],
        verbose_name=_("level of consciousness (ACVPU)"),
    )

    # -- measurements that are not NEWS2 ------------------------------------
    weight_kg = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.3")), MaxValueValidator(Decimal(400))],
    )
    height_cm = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal(20)), MaxValueValidator(Decimal(260))],
    )
    #: Measured in mg/dL, which is what Nigerian glucometers read in.
    blood_glucose = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        verbose_name=_("blood glucose, mg/dL"),
    )
    pain_score = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(10)], help_text=_("0 to 10.")
    )
    position = models.CharField(max_length=10, choices=Position, blank=True)

    # -- the score ----------------------------------------------------------
    news2_score = models.PositiveSmallIntegerField(null=True, blank=True)
    #: True when one parameter alone scored 3 — escalates whatever the total.
    news2_red_flag = models.BooleanField(default=False)
    news2_band = models.CharField(max_length=16, blank=True)
    #: Which revision produced the stored score. A later revision must not
    #: silently rewrite what a nurse was shown.
    score_version = models.CharField(max_length=16, blank=True, default="NEWS2-2017")
    #: Scale 2 is for hypercapnic respiratory failure and has to be chosen by
    #: a clinician — see apps/clinical/news2.py.
    oxygen_scale = models.PositiveSmallIntegerField(default=1)

    note = models.CharField(max_length=300, blank=True)

    #: Set when a later observation corrects this one. Never deleted: a
    #: reading that was reacted to is a fact even once it is known to be wrong.
    superseded_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="supersedes"
    )
    correction_reason = models.CharField(max_length=200, blank=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("observation")
        ordering = ("-taken_at",)
        indexes = [
            models.Index(fields=["organisation", "patient", "-taken_at"]),
            models.Index(fields=["encounter", "-taken_at"]),
            # The ward round's query: everybody whose last score was high.
            models.Index(fields=["branch", "news2_band", "-taken_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.patient_id} at {self.taken_at:%Y-%m-%d %H:%M}"

    @property
    def is_current(self) -> bool:
        return self.superseded_by_id is None

    @property
    def blood_pressure(self) -> str:
        if self.systolic_bp and self.diastolic_bp:
            return f"{self.systolic_bp}/{self.diastolic_bp}"
        return str(self.systolic_bp or "")

    def clean(self) -> None:
        super().clean()
        if self.systolic_bp and self.diastolic_bp and self.diastolic_bp >= self.systolic_bp:
            raise ValidationError(
                {
                    "diastolic_bp": _(
                        "The lower number has to be below the upper one. They may have "
                        "been entered the wrong way round."
                    )
                }
            )
        if self.on_oxygen is False and self.oxygen_litres:
            raise ValidationError(
                {"oxygen_litres": _("A flow rate was given but the patient is on air.")}
            )

    def recorded_anything(self) -> bool:
        """
        True when this is a real observation rather than an empty form.

        Checked before saving: a row of nothing but a timestamp is noise on a
        chart that people scan for change.
        """
        return any(
            value is not None and value != ""
            for value in (
                self.respiration_rate,
                self.spo2,
                self.systolic_bp,
                self.pulse,
                self.temperature,
                self.consciousness,
                self.weight_kg,
                self.height_cm,
                self.blood_glucose,
                self.pain_score,
            )
        )


class TriageAssessment(BranchScoped):
    """
    The decision about how urgent somebody is.

    Separate from the observations because it is a judgement rather than a
    measurement, it is made once per encounter (and occasionally revised),
    and it is what the queue is ordered by. A nurse can raise a patient's
    category on a feeling with no abnormal vital sign in sight, and must be
    able to.
    """

    encounter = models.OneToOneField(
        "journey.Encounter", on_delete=models.PROTECT, related_name="triage"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="triage_assessments"
    )

    category = models.CharField(max_length=8, choices=TriageCategory, db_index=True)
    complaint = models.CharField(
        max_length=200, help_text=_("What the patient says is wrong, in their words.")
    )
    #: The observation the decision was made alongside, where there was one.
    observation = models.ForeignKey(
        Observation, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    assessed_at = models.DateTimeField(default=timezone.now, db_index=True)
    assessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    #: Filled when the category is changed. Required by the service, because
    #: downgrading somebody from orange to green is a decision that should
    #: have a name and a sentence attached to it.
    revision_reason = models.CharField(max_length=200, blank=True)
    revised_at = models.DateTimeField(null=True, blank=True)
    revised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta(BranchScoped.Meta):
        verbose_name = _("triage assessment")
        ordering = ("-assessed_at",)
        indexes = [models.Index(fields=["branch", "category", "-assessed_at"])]

    def __str__(self) -> str:
        return f"{self.get_category_display()} — {self.complaint[:40]}"

    @property
    def target_minutes(self) -> int | None:
        return TRIAGE_TARGET_MINUTES.get(self.category)

    def is_overdue(self, *, now: dt.datetime | None = None) -> bool:
        """
        Waited past the promise their own colour made.

        The only wait that means anything clinically: a green patient at 90
        minutes is fine, and a red one at five is a crisis.
        """
        target = self.target_minutes
        if target is None:
            return False
        waited = ((now or timezone.now()) - self.assessed_at).total_seconds() / 60
        return waited > target


class NursingNote(BranchScoped):
    """
    What a nurse wrote.

    Short, timestamped, and **never edited** — an addendum is added instead,
    pointing at the note it follows. A nursing record that can be rewritten
    after an incident is a nursing record nobody can rely on, and the person
    it protects first is the nurse.
    """

    class Kind(models.TextChoices):
        OBSERVATION = "observation", _("Nursing observation")
        CARE = "care", _("Care given")
        HANDOVER = "handover", _("Handover")
        INCIDENT = "incident", _("Incident")
        ADDENDUM = "addendum", _("Addendum")

    encounter = models.ForeignKey(
        "journey.Encounter", on_delete=models.PROTECT, related_name="nursing_notes"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="nursing_notes"
    )

    kind = models.CharField(max_length=16, choices=Kind, default=Kind.OBSERVATION)
    body = models.TextField()

    written_at = models.DateTimeField(default=timezone.now, db_index=True)
    written_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    #: An addendum points at what it is adding to. The original is untouched.
    amends = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="addenda"
    )

    class Meta(BranchScoped.Meta):
        verbose_name = _("nursing note")
        ordering = ("-written_at",)
        indexes = [
            models.Index(fields=["organisation", "patient", "-written_at"]),
            models.Index(fields=["encounter", "written_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.written_at:%Y-%m-%d %H:%M}"

    def clean(self) -> None:
        super().clean()
        if not self.body.strip():
            raise ValidationError({"body": _("A note needs something in it.")})
        if self.amends_id and self.kind != self.Kind.ADDENDUM:
            raise ValidationError({"kind": _("A note that amends another one is an addendum.")})
