"""
The encounter, and the journey it takes through the hospital.

**The encounter is the spine of Curavia.** One visit, from the moment somebody
arrives to the moment they leave. Every clinical and financial record that
arrives in later milestones hangs off it: the triage observations, the
consultation note, the lab orders, the prescription, the bill. That is the
decision that makes "one system, every department" mean something — without it
each module keeps its own idea of a visit and nothing can be joined up
afterwards.

Three things about the design are worth stating plainly.

**The stage is a column, and the history is a table.** ``Encounter.stage``
answers "where is this patient now", which is the question the board asks
sixty times a minute and must answer without scanning anything.
``JourneyEvent`` answers "how did they get here and how long did each part
take", which is the question management asks once a month. Keeping both is
duplication of a kind, and the alternative — deriving the current stage by
replaying events on every read — makes the cheap question as expensive as the
dear one.

**Every transition stores how long the previous stage took.** Computed at the
moment of the move, not later from timestamps. It is a fact about what
happened, it makes "average wait before the doctor" one query rather than a
window function over the whole table, and it survives a clock change.

**A waiting-room display is a tenant of its own.** The television in the
reception is not a member of staff, has no password, and is watched by
everyone in the room including people who do not work here. It gets its own
token, its own consumer and — crucially — a payload with no names in it. See
``DisplayScreen``.
"""

from __future__ import annotations

import datetime as dt
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BranchScoped


class Stage(models.TextChoices):
    """
    Where a patient is, in the order they usually pass through.

    The order matters: it is what the board's columns are drawn from and what
    ``Stage.order_of`` uses to decide whether a move is forward or back. It is
    an ordering, not a workflow — a patient can go from the doctor straight to
    the cashier without a lab test, and plenty go round twice.
    """

    ARRIVED = "arrived", _("Arrived")
    TRIAGE = "triage", _("Waiting for triage")
    IN_TRIAGE = "in_triage", _("In triage")
    WAITING_DOCTOR = "waiting_doctor", _("Waiting for the doctor")
    WITH_DOCTOR = "with_doctor", _("With the doctor")
    LAB = "lab", _("Laboratory")
    RADIOLOGY = "radiology", _("Radiology")
    PHARMACY = "pharmacy", _("Pharmacy")
    CASHIER = "cashier", _("Cashier")
    WARD = "ward", _("Admitted to a ward")
    DONE = "done", _("Finished")
    LEFT = "left", _("Left without being seen")

    @classmethod
    def order_of(cls, value: str) -> int:
        order = [choice.value for choice in cls]
        return order.index(value) if value in order else len(order)


#: Stages where the patient is waiting for somebody rather than being seen.
#: The board counts these towards the wait; the others are time being spent
#: on the patient, which is not the same thing and must not be added to it.
WAITING_STAGES: frozenset[str] = frozenset(
    {Stage.ARRIVED, Stage.TRIAGE, Stage.WAITING_DOCTOR, Stage.LAB, Stage.PHARMACY, Stage.CASHIER}
)

#: Stages that end the encounter.
CLOSING_STAGES: frozenset[str] = frozenset({Stage.DONE, Stage.LEFT})


class Encounter(BranchScoped):
    """One visit."""

    class Kind(models.TextChoices):
        OUTPATIENT = "outpatient", _("Outpatient")
        EMERGENCY = "emergency", _("Emergency")
        INPATIENT = "inpatient", _("Inpatient")
        TELEMEDICINE = "telemedicine", _("Video consultation")

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        CLOSED = "closed", _("Closed")
        CANCELLED = "cancelled", _("Cancelled")

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="encounters"
    )
    kind = models.CharField(max_length=16, choices=Kind, default=Kind.OUTPATIENT, db_index=True)
    status = models.CharField(max_length=12, choices=Status, default=Status.OPEN, db_index=True)

    #: Where they are now. See the module docstring for why this is a column
    #: as well as a table of events.
    stage = models.CharField(max_length=20, choices=Stage, default=Stage.ARRIVED, db_index=True)
    stage_since = models.DateTimeField(
        default=timezone.now,
        help_text=_("When they entered the stage they are in. The board's clock."),
    )
    department = models.ForeignKey(
        "core.Department",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="encounters",
    )

    appointment = models.OneToOneField(
        "scheduling.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="encounter",
    )
    ticket = models.OneToOneField(
        "scheduling.QueueTicket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="encounter",
    )

    attending = models.ForeignKey(
        "accounts.StaffMembership",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="encounters",
    )

    #: Red, orange, yellow, green, blue — set by triage at milestone 6. Stored
    #: here as well as on the triage record so the board can colour a row
    #: without joining to a module that may not be switched on.
    triage_category = models.CharField(max_length=12, blank=True, db_index=True)

    reason = models.CharField(max_length=200, blank=True)

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta(BranchScoped.Meta):
        verbose_name = _("encounter")
        ordering = ("-started_at",)
        indexes = [
            # The board's query: this branch, open, ordered by how long they
            # have been standing there.
            models.Index(fields=["branch", "status", "stage_since"]),
            models.Index(fields=["organisation", "patient", "-started_at"]),
            models.Index(fields=["organisation", "status", "stage"]),
        ]
        constraints = [
            # One open visit per patient per branch. Somebody checked in twice
            # by two clerks is one person in the building, and two rows on the
            # board is how a patient gets seen twice and billed twice.
            models.UniqueConstraint(
                fields=["branch", "patient"],
                condition=models.Q(status="open"),
                name="one_open_encounter_per_patient_per_branch",
            )
        ]

    def __str__(self) -> str:
        return f"{self.patient_id} {self.started_at:%Y-%m-%d} ({self.stage})"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OPEN

    @property
    def is_waiting(self) -> bool:
        return self.stage in WAITING_STAGES

    def minutes_in_stage(self, now: dt.datetime | None = None) -> int:
        return max(int(((now or timezone.now()) - self.stage_since).total_seconds() // 60), 0)

    def minutes_in_hospital(self, now: dt.datetime | None = None) -> int:
        end = self.ended_at or now or timezone.now()
        return max(int((end - self.started_at).total_seconds() // 60), 0)

    def clean(self) -> None:
        super().clean()
        if self.ended_at and self.ended_at < self.started_at:
            raise ValidationError({"ended_at": _("A visit cannot end before it started.")})


class JourneyEvent(BranchScoped):
    """
    One move from one stage to the next. Append-only.

    Never updated and never deleted: this is where "the patient waited two
    hours and ten minutes before anybody saw them" comes from, and a table
    that can be edited after the fact cannot answer that question to anybody
    who matters.
    """

    encounter = models.ForeignKey(Encounter, on_delete=models.PROTECT, related_name="events")
    from_stage = models.CharField(max_length=20, blank=True)
    to_stage = models.CharField(max_length=20, choices=Stage)
    department = models.ForeignKey(
        "core.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    at = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    #: How long the stage being left had lasted. Written at the moment of the
    #: move; see the module docstring.
    minutes_in_previous_stage = models.PositiveIntegerField(default=0)
    #: True when the previous stage was one the patient spent waiting, so a
    #: report can add up waiting without re-deciding what counts.
    previous_was_waiting = models.BooleanField(default=False)

    note = models.CharField(max_length=200, blank=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("journey event")
        ordering = ("at",)
        indexes = [
            models.Index(fields=["organisation", "encounter", "at"]),
            models.Index(fields=["branch", "at"]),
        ]

    def __str__(self) -> str:
        return f"{self.from_stage or 'start'} → {self.to_stage}"


def _new_screen_token() -> str:
    """
    A token for a television.

    32 bytes of URL-safe randomness: it goes in a URL that is typed into a
    browser once and then left open for years, so it has to be unguessable
    and does not have to be short.
    """
    return secrets.token_urlsafe(32)


class DisplayScreen(BranchScoped):
    """
    A television in a waiting room.

    It is not a member of staff. It has no password, nobody signs it in, and
    it is watched by everybody in the room — including the people sitting in
    the queue, their relatives, and whoever walked in off the street.

    So it gets its own token in its own URL, and the payload it receives has
    **no patient names in it**. Only token numbers, the room being called to,
    and counts. That is not a configuration option: a waiting-room screen
    that can show names is a waiting-room screen that will, the first time
    somebody thinks it would be helpful.

    ``departments`` narrows what one screen shows, so the pharmacy television
    is not calling out the antenatal queue.
    """

    name = models.CharField(max_length=80, help_text=_("Where it is: 'Main reception'."))
    token = models.CharField(max_length=64, unique=True, default=_new_screen_token, editable=False)
    departments = models.ManyToManyField(
        "core.Department",
        blank=True,
        related_name="display_screens",
        help_text=_("Leave empty to show every queue at this branch."),
    )
    #: Shown under the called number, so a patient knows where to walk.
    footer = models.CharField(
        max_length=200, blank=True, help_text=_("A line of help shown along the bottom.")
    )
    is_active = models.BooleanField(default=True, db_index=True)
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When this screen last connected. A dark television is a support call."),
    )

    class Meta(BranchScoped.Meta):
        verbose_name = _("display screen")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "name"], name="unique_display_name_per_branch"
            )
        ]

    def __str__(self) -> str:
        return self.name

    def rotate_token(self) -> str:
        """
        Issue a new token and invalidate the old URL.

        Used when a screen is moved, replaced, or when somebody photographs
        the address bar. The old URL stops working immediately.
        """
        self.token = _new_screen_token()
        self.save(update_fields=["token", "updated_at"])
        return self.token
