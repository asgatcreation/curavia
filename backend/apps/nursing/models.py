"""
What the nurses on this shift have to do, and what they tell the next shift.

One thing decides the shape of everything here:

    **The commonest harm on a ward is something that fell between two
    shifts.**

Not a wrong drug or a missed diagnosis — those happen, and they are rarer.
What happens every week is that a nurse meant to do something, got pulled
away, and the person who came on at two o'clock never knew it was
outstanding. So:

* **A task is never deleted.** It is done, with a time and a name against
  it, or it is not done, with a reason. A task list that can be tidied up is
  a task list that gets tidied up at the end of a bad shift.

* **The handover's outstanding list is built by the system.** A handover
  sheet typed from memory omits precisely the thing nobody remembered —
  that is what "forgot" means. The nurse writes the judgement; the system
  supplies the list.

* **A handover is signed by the nurse going off and accepted by the nurse
  coming on.** Both halves, because a handover nobody received is a
  handover that did not happen, and the ward needs to be able to say which
  of the two it was.

* **A signed handover never changes**, by the same rule and the same
  mechanism as a clinical note: a hash over the content, checked in
  ``save()``.
"""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BranchScoped


def hash_content(content: dict | None) -> str:
    """Canonical JSON, then sha256 — the same scheme the clinical note uses."""
    canonical = json.dumps(content or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaskKind(models.TextChoices):
    """
    What sort of thing it is.

    Kept short, and about what the nurse does rather than which department
    asked. A list with twenty categories is a list nobody filters.
    """

    OBSERVATION = "observation", _("Observations")
    MEDICATION = "medication", _("Give a medicine")
    CARE = "care", _("Nursing care")
    SAMPLE = "sample", _("Take a sample")
    REVIEW = "review", _("Ask for a review")
    OTHER = "other", _("Other")


class TaskStatus(models.TextChoices):
    PENDING = "pending", _("To do")
    DONE = "done", _("Done")
    NOT_DONE = "not_done", _("Not done")
    CANCELLED = "cancelled", _("Cancelled")


#: Statuses that still need somebody to act. Everything else is settled.
OPEN_STATUSES = (TaskStatus.PENDING,)


class NursingTask(BranchScoped):
    """
    One thing to do for one patient, by a particular time.

    Three sources, and the first two matter most because they are the ones
    nobody has to remember to create:

    * a signed order of a kind a nurse carries out — the doctor's
      instruction becomes the nurse's task without anybody retyping it;
    * observations falling due under the NEWS2 band already computed at
      triage, so "she is due her obs" is on a list rather than in somebody's
      head;
    * a task a nurse adds, because wards are not fully describable.

    ``due_at`` is what the list is sorted by and what "overdue" is measured
    from. It is never guessed: a task with no sensible due time is given
    none and sorts after the ones that have one, rather than being given a
    made-up deadline that then goes red on the screen for no reason.
    """

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="nursing_tasks"
    )
    encounter = models.ForeignKey(
        "journey.Encounter",
        on_delete=models.PROTECT,
        related_name="nursing_tasks",
        null=True,
        blank=True,
    )
    #: Which ward or clinic's list this belongs on.
    department = models.ForeignKey(
        "core.Department",
        on_delete=models.PROTECT,
        related_name="nursing_tasks",
        null=True,
        blank=True,
    )

    kind = models.CharField(max_length=16, choices=TaskKind, default=TaskKind.CARE)
    name = models.CharField(max_length=200)
    detail = models.TextField(blank=True)

    #: The order this came from, where it came from one. Kept so the ward can
    #: see the instruction behind the task, and so one order does not
    #: generate a second task every time the list is rebuilt.
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="nursing_tasks",
        null=True,
        blank=True,
    )

    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    #: Copied from the order where there is one, so a stat instruction still
    #: reads as stat on the nurse's list.
    urgent = models.BooleanField(default=False)

    status = models.CharField(max_length=16, choices=TaskStatus, default=TaskStatus.PENDING)

    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nursing_tasks_done",
        null=True,
        blank=True,
    )
    done_by_name = models.CharField(max_length=200, blank=True)
    #: What was actually done, where that differs from what was asked.
    done_note = models.TextField(blank=True)
    #: Required when a task is closed without being done. "Patient off the
    #: ward for a scan" and "refused" are different clinical facts, and a
    #: blank here is how a ward loses the difference.
    not_done_reason = models.TextField(blank=True)

    #: How many shifts this has been passed along. A task on its third
    #: handover is a task the ward should be asked about, and counting is
    #: cheaper than anybody noticing.
    handovers = models.PositiveSmallIntegerField(default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="nursing_tasks_raised",
        null=True,
        blank=True,
    )

    class Meta(BranchScoped.Meta):
        verbose_name = _("nursing task")
        # Urgent first, then by when it is due. Tasks with no due time sort
        # last rather than first: a null that sorted to the top would push
        # real deadlines off the screen.
        ordering = ("-urgent", models.F("due_at").asc(nulls_last=True), "created_at")
        constraints = [
            # One task per order, so rebuilding the list is safe to run as
            # often as the screen likes.
            models.UniqueConstraint(
                fields=["order"],
                condition=models.Q(order__isnull=False),
                name="nursing_task_one_per_order",
            ),
        ]
        indexes = [
            models.Index(fields=["organisation", "status", "due_at"]),
            models.Index(fields=["patient", "status"]),
            models.Index(fields=["department", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_status_display()})"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def is_overdue(self) -> bool:
        return bool(self.is_open and self.due_at and self.due_at < timezone.now())

    @property
    def minutes_late(self) -> int:
        """How late it is, in whole minutes. Zero when it is not late."""
        if not self.is_overdue or not self.due_at:
            return 0
        return int((timezone.now() - self.due_at).total_seconds() // 60)

    def clean(self) -> None:
        super().clean()
        if not self.name.strip():
            raise ValidationError({"name": _("Say what has to be done.")})
        if self.status == TaskStatus.NOT_DONE and not self.not_done_reason.strip():
            raise ValidationError(
                {"not_done_reason": _("Say why this was not done. It is not the same as done.")}
            )


class HandoverStatus(models.TextChoices):
    DRAFT = "draft", _("Being written")
    SIGNED = "signed", _("Handed over")
    ACCEPTED = "accepted", _("Received")


class Handover(BranchScoped):
    """
    One shift telling the next what it needs to know.

    The content is SBAR — Situation, Background, Assessment, Recommendation —
    per patient, because that is the structure every Nigerian nursing school
    teaches and a free-text box produces four paragraphs about the first
    patient and nothing about the eleventh.

    ``outstanding`` is a snapshot, taken at the moment of signing, of every
    task still open on this ward. It is not a link to the live list: the
    question a week later is "what was this nurse told was outstanding when
    she came on", and a live query answers a different question.

    Accepting is a separate act by the nurse coming on. A handover nobody
    received is a handover that did not happen, and the ward has to be able
    to tell that apart from one that was never written.
    """

    department = models.ForeignKey(
        "core.Department", on_delete=models.PROTECT, related_name="handovers"
    )
    #: Free text, e.g. "Morning to afternoon". Shifts proper arrive with the
    #: rota at milestone 11; until then the ward says what it calls them.
    shift = models.CharField(max_length=60, blank=True)

    from_nurse = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="handovers_given"
    )
    from_nurse_name = models.CharField(max_length=200, blank=True)
    from_nurse_role = models.CharField(max_length=120, blank=True)

    to_nurse = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="handovers_received",
        null=True,
        blank=True,
    )
    to_nurse_name = models.CharField(max_length=200, blank=True)

    status = models.CharField(max_length=16, choices=HandoverStatus, default=HandoverStatus.DRAFT)

    #: ``{"patients": [{"patient_id", "name", "situation", "background",
    #: "assessment", "recommendation"}], "ward_note": "..."}``
    content = models.JSONField(default=dict, blank=True)
    #: What was still open when this was signed, frozen.
    outstanding = models.JSONField(default=list, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)

    signed_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    #: What the nurse coming on said when she took it. Optional — most
    #: handovers are accepted without comment, and a required box there
    #: would be filled with a full stop.
    accepted_note = models.TextField(blank=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("handover")
        ordering = ("-created_at",)
        constraints = [
            # One handover being written per nurse per ward at a time. Two
            # half-written sheets is how a ward hands over twice and means
            # neither.
            models.UniqueConstraint(
                fields=["department", "from_nurse"],
                condition=models.Q(status="draft"),
                name="handover_one_draft_per_nurse_per_ward",
            ),
        ]
        indexes = [
            models.Index(fields=["department", "-created_at"]),
            models.Index(fields=["organisation", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.department_id} {self.shift or ''} ({self.get_status_display()})"

    @property
    def is_signed(self) -> bool:
        return self.status in (HandoverStatus.SIGNED, HandoverStatus.ACCEPTED)

    @property
    def patient_count(self) -> int:
        patients = self.content.get("patients", [])
        return len(patients) if isinstance(patients, list) else 0

    @property
    def outstanding_count(self) -> int:
        return len(self.outstanding) if isinstance(self.outstanding, list) else 0

    def signature_block(self) -> list[str]:
        """Empty until signed, so a draft cannot be screenshotted as a handover."""
        if not self.is_signed:
            return []
        lines = [self.from_nurse_name]
        if self.from_nurse_role:
            lines.append(self.from_nurse_role)
        if self.signed_at:
            # The ward's own clock. A handover timed in UTC is a handover
            # that appears to have happened an hour before the shift did.
            signed = timezone.localtime(self.signed_at)
            lines.append(f"Handed over {signed:%d %b %Y at %H:%M}")
        if self.accepted_at and self.to_nurse_name:
            accepted = timezone.localtime(self.accepted_at)
            lines.append(f"Received by {self.to_nurse_name} at {accepted:%H:%M}")
        return [line for line in lines if line]

    def save(self, *args, **kwargs) -> None:
        if self.is_signed and self.content_hash and hash_content(self.content) != self.content_hash:
            raise ValidationError(
                _(
                    "This handover has been given to the next shift. It cannot be "
                    "rewritten — write a new note instead."
                )
            )
        super().save(*args, **kwargs)


__all__ = [
    "OPEN_STATUSES",
    "Handover",
    "HandoverStatus",
    "NursingTask",
    "TaskKind",
    "TaskStatus",
    "hash_content",
]
