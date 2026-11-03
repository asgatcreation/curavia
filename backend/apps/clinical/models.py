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
import hashlib
import json
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.clinical import news2, note_templates
from apps.core.models import BranchScoped, TenantScoped


def hash_content(content: dict | None) -> str:
    """
    A stable fingerprint of a note's wording.

    Canonical JSON — keys sorted, no insignificant whitespace — so the same
    note hashes the same however the client happened to order its fields,
    and so a hash computed on a signed note years later still matches. The
    hash is what a signature is over, what ``ClinicalNote.save()`` checks,
    and what a QR code on a printed note will verify against.
    """
    canonical = json.dumps(content or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


class ClinicalNote(BranchScoped):
    """
    What a clinician wrote about one visit.

    Three properties, in the order they mattered while this was designed.

    **A signed note never changes.** Signing computes a hash of the content
    and stores it; ``save()`` refuses to write a signed row whose content no
    longer matches its hash, wherever in the codebase that write comes from.
    A clinical record that can be quietly rewritten after a complaint is not
    a record, and the person it protects first is the clinician.

    **An amendment is a new version, not an edit.** ``amend`` opens version
    N+1 as a draft pointing at the version it follows, with a reason. The
    earlier version stays signed and readable forever. What was known, and
    when, is the whole question in a case review.

    **A draft is a working document.** Before it is signed, the author's
    autosave overwrites it as often as they type, because the alternative is
    a doctor losing a consultation to a dropped connection. The draft is
    nobody else's evidence yet: it is marked as unsigned everywhere it is
    shown, and nothing downstream acts on it.

    ``thread`` ties the versions together: every version of one note carries
    the first version's id, so the history is one query rather than a walk
    up the ``supersedes`` chain.

    The content is JSON keyed by the section keys of its template — see
    apps/clinical/note_templates.py. Keyed rather than positional, so a
    template that gains a section later still reads back correctly, and
    stored rather than referenced, so changing a template cannot alter the
    words somebody signed.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SIGNED = "signed", _("Signed")

    encounter = models.ForeignKey(
        "journey.Encounter", on_delete=models.PROTECT, related_name="clinical_notes"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="clinical_notes"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="clinical_notes"
    )

    template_key = models.CharField(max_length=32, default=note_templates.DEFAULT_TEMPLATE)
    content = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=8, choices=Status, default=Status.DRAFT, db_index=True)

    #: The first version's id, carried by every version of the same note.
    thread = models.UUIDField(db_index=True)
    version = models.PositiveSmallIntegerField(default=1)
    #: The version this one amends. Null on a first version.
    supersedes = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="superseded_by_note"
    )
    #: True on the newest version of the thread. The one a screen shows.
    is_current = models.BooleanField(default=True)
    amendment_reason = models.CharField(max_length=300, blank=True)

    signed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    # -- the signature, snapshotted -----------------------------------------
    #
    # Copied onto the note at the moment of signing rather than read through
    # the author's profile. A consultant who leaves, changes their name, or
    # has a registration number corrected must not retroactively alter what
    # is printed under a note they signed two years ago.
    signature_name = models.CharField(max_length=160, blank=True)
    signature_qualifications = models.CharField(max_length=120, blank=True)
    signature_registration = models.CharField(max_length=60, blank=True)
    signature_role = models.CharField(max_length=120, blank=True)

    #: SHA-256 of the canonical content at the moment of signing. Also what a
    #: QR-verifiable PDF of this note will be checked against.
    content_hash = models.CharField(max_length=64, blank=True)

    #: Needs ``clinical.sensitive.view`` to read. Set by the author, because
    #: only they know that the note names an HIV result or a psychiatric
    #: history; field-level encryption arrives with the key in .env.
    is_sensitive = models.BooleanField(default=False)

    last_saved_at = models.DateTimeField(default=timezone.now)

    class Meta(BranchScoped.Meta):
        verbose_name = _("clinical note")
        ordering = ("-signed_at", "-last_saved_at")
        constraints = [
            # One open draft per clinician, per visit, per template. A second
            # one is always an accident — two tabs, or a double-click on
            # "start a note" — and it would split one consultation in half.
            models.UniqueConstraint(
                fields=["encounter", "author", "template_key"],
                # A discarded draft is archived rather than deleted, and must
                # not hold the slot against the next one.
                condition=models.Q(status="draft", archived_at__isnull=True),
                name="one_draft_note_per_author_per_template",
            ),
            models.UniqueConstraint(
                fields=["thread", "version"], name="unique_version_per_note_thread"
            ),
        ]
        indexes = [
            models.Index(fields=["organisation", "patient", "-signed_at"]),
            models.Index(fields=["encounter", "status"]),
            models.Index(fields=["thread", "version"]),
        ]

    def __str__(self) -> str:
        return f"{self.template_key} v{self.version} ({self.status})"

    @property
    def is_signed(self) -> bool:
        return self.status == self.Status.SIGNED

    @property
    def is_amendment(self) -> bool:
        return self.version > 1

    def save(self, *args, **kwargs) -> None:
        """
        Refuse to write a signed note whose content has been changed.

        The last line of defence rather than the first — the service raises a
        clear error long before this — but it is the one that holds for code
        written later by somebody who does not know the rule. A guard that
        only lives in a service function is a guard that will be walked
        around the first time somebody uses ``.update()`` or the admin.
        """
        if (
            self.status == self.Status.SIGNED
            and self.content_hash
            and hash_content(self.content) != self.content_hash
        ):
            raise ValidationError(
                _("This note is signed. Its wording cannot be changed — add an amendment instead.")
            )
        super().save(*args, **kwargs)

    def signature_block(self) -> list[str]:
        """
        The stamp, as the lines it prints on a screen or a PDF.

        Empty until the note is signed, so a draft cannot be made to look
        like a signed document by a screenshot.
        """
        if not self.is_signed:
            return []
        lines = [self.signature_name]
        if self.signature_qualifications:
            lines.append(self.signature_qualifications)
        if self.signature_registration:
            lines.append(self.signature_registration)
        if self.signature_role:
            lines.append(self.signature_role)
        if self.signed_at:
            # Lagos time, not UTC. Found in the browser: the stamp read
            # "08:50" while the list beside it read "09:50", because a
            # stored datetime is UTC and only `localtime` puts it back into
            # the hospital's own clock. An hour's disagreement on the face of
            # a signed document is the kind of detail a lawyer reads out.
            signed = timezone.localtime(self.signed_at)
            lines.append(f"Signed {signed:%d %b %Y at %H:%M}")
        if self.content_hash:
            # Eight characters is enough to compare two printouts by eye and
            # short enough to read aloud down a telephone.
            lines.append(f"Reference {self.content_hash[:8].upper()}")
        return [line for line in lines if line]


class Certainty(models.TextChoices):
    """
    How sure the clinician is.

    Recorded because a working diagnosis and a confirmed one are different
    facts, and a record that cannot tell them apart produces two errors: a
    patient treated for years for something nobody ever confirmed, and a
    statistics return that counts suspicions as cases.
    """

    SUSPECTED = "suspected", _("Suspected")
    CONFIRMED = "confirmed", _("Confirmed")
    RULED_OUT = "ruled_out", _("Ruled out")


class Diagnosis(BranchScoped):
    """
    What was decided on one visit.

    The code and its wording are **both stored**. The catalogue in
    apps/clinical/icd10.py may be corrected or replaced, and what a clinician
    chose on the day must read the same in five years' time.

    Nothing here is deleted. A diagnosis that turns out to be wrong is marked
    ``ruled_out`` — which is information, and often the most useful line in a
    record, because it tells the next doctor what has already been considered
    and excluded.
    """

    encounter = models.ForeignKey(
        "journey.Encounter", on_delete=models.PROTECT, related_name="diagnoses"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="diagnoses"
    )

    code = models.CharField(max_length=12, db_index=True)
    #: The wording as it was at the time. See the class docstring.
    title = models.CharField(max_length=200)
    chapter = models.CharField(max_length=60, blank=True)

    certainty = models.CharField(max_length=12, choices=Certainty, default=Certainty.SUSPECTED)
    #: Exactly one primary per visit — the reason they came. The service
    #: demotes the previous one rather than allowing two.
    is_primary = models.BooleanField(default=False)
    #: Snapshotted from the catalogue: whether this is something the patient
    #: has rather than something they are getting over.
    is_chronic = models.BooleanField(default=False)

    note = models.CharField(max_length=300, blank=True)

    diagnosed_at = models.DateTimeField(default=timezone.now, db_index=True)
    diagnosed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta(BranchScoped.Meta):
        verbose_name = _("diagnosis")
        verbose_name_plural = _("diagnoses")
        ordering = ("-is_primary", "diagnosed_at")
        constraints = [
            # The same code twice on one visit is always a slip — two doctors
            # on the same ward round, or a double-click — and it would double
            # count the patient in every return the hospital files.
            models.UniqueConstraint(
                fields=["encounter", "code"],
                condition=models.Q(archived_at__isnull=True),
                name="one_diagnosis_per_code_per_encounter",
            )
        ]
        indexes = [
            models.Index(fields=["organisation", "patient", "-diagnosed_at"]),
            models.Index(fields=["branch", "code", "-diagnosed_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.title}"

    @property
    def label(self) -> str:
        return f"{self.code} · {self.title}"


class Problem(TenantScoped):
    """
    The problem list: what this patient has, across every visit.

    A hospital record without one is a pile of visits. The problem list is
    what a doctor reads in the fifteen seconds before the patient sits down,
    and the thing the pharmacy, the laboratory and the ward all need in
    order to interpret anything else.

    **One row per condition per patient, ever.** Resolving it sets a date;
    it does not delete the row, and it does not stop the same condition being
    reopened later — that is the same problem coming back, not a new one. The
    alternative, a row per episode, produces a list with "Type 2 diabetes"
    on it four times, which is how a real problem list becomes unreadable and
    then unread.

    Not branch-scoped: a patient's conditions belong to them, not to the
    building they were sitting in when somebody wrote them down.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        RESOLVED = "resolved", _("Resolved")
        INACTIVE = "inactive", _("Inactive")

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="problems"
    )

    code = models.CharField(max_length=12, db_index=True)
    title = models.CharField(max_length=200)
    chapter = models.CharField(max_length=60, blank=True)

    status = models.CharField(max_length=10, choices=Status, default=Status.ACTIVE, db_index=True)
    onset_on = models.DateField(null=True, blank=True)
    resolved_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True)

    #: The diagnosis that opened it, where one did. Null for a problem added
    #: straight to the list — a condition the patient arrives already having.
    opened_by_diagnosis = models.ForeignKey(
        Diagnosis, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    opened_at = models.DateTimeField(default=timezone.now)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    last_noted_at = models.DateTimeField(default=timezone.now)

    class Meta(TenantScoped.Meta):
        verbose_name = _("problem")
        ordering = ("status", "-last_noted_at")
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "patient", "code"],
                name="one_problem_per_code_per_patient",
            )
        ]
        indexes = [models.Index(fields=["organisation", "patient", "status"])]

    def __str__(self) -> str:
        return f"{self.code} {self.title} ({self.status})"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def label(self) -> str:
        return f"{self.code} · {self.title}"
