"""
Clinics, who runs them, when they are not running, and who is booked in.

Five tables, and the shape of each is decided by one thing: **a Nigerian
outpatient clinic is a queue with appointments attached, not a diary.**

Western scheduling software assumes a patient arrives at 10:15 for a 10:15
slot. Here, a booked patient and four walk-ins arrive at eight o'clock, and
the booking is a claim on a position rather than a promise about a minute. So:

* **Appointments and queue tickets are separate tables that meet at the
  desk.** A booking is an intention made in advance; a ticket is a position
  in today's queue. Checking in turns the first into the second. A walk-in
  gets a ticket and never has an appointment, which is most patients, and the
  system must not treat them as an exception.

* **Slots are computed, never stored.** Materialising a year of empty rows
  for every doctor is a table that is almost entirely nothing, and it goes
  stale the moment a rota changes. Availability is derived from the schedule
  and the bookings that exist; see ``availability.py``.

* **Leave does not cancel anybody's appointment.** Entering a doctor's
  annual leave removes future availability and *flags* the bookings already
  made, because silently cancelling fifty people — who were told a date and
  have arranged transport from Ogun State — is not a thing software should
  do on its own. See ``ScheduleException``.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BranchScoped

#: The shortest slot the system will issue. Five minutes is already shorter
#: than any real consultation; anything less is a data-entry slip.
MIN_SLOT_MINUTES = 5
MAX_SLOT_MINUTES = 240


class Weekday(models.IntegerChoices):
    """
    Monday is 0, matching ``date.weekday()``.

    Deliberately not Django's ``__week_day`` (Sunday is 1) or ISO (Monday is
    1): every comparison in this module is against a Python ``date``, and one
    convention consistently applied is worth more than matching any
    particular external one.
    """

    MONDAY = 0, _("Monday")
    TUESDAY = 1, _("Tuesday")
    WEDNESDAY = 2, _("Wednesday")
    THURSDAY = 3, _("Thursday")
    FRIDAY = 4, _("Friday")
    SATURDAY = 5, _("Saturday")
    SUNDAY = 6, _("Sunday")


class Clinic(BranchScoped):
    """
    A named clinic that runs at a department: "General Outpatient — Morning".

    Separate from ``Department`` because one department runs several clinics
    with different doctors, days and prices — a GOPD morning clinic and an
    evening clinic are the same room and not the same thing to book into.

    ``service`` is what the visit bills as. Nullable, because a hospital that
    has not finished pricing should still be able to run a diary; the bill
    then falls back to whatever the cashier chooses, and the setup checklist
    says so.
    """

    department = models.ForeignKey(
        "core.Department", on_delete=models.PROTECT, related_name="clinics"
    )
    name = models.CharField(max_length=120)
    service = models.ForeignKey(
        "catalogue.Service",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="clinics",
        help_text=_("What a visit to this clinic bills as."),
    )
    default_duration_minutes = models.PositiveSmallIntegerField(
        default=15,
        validators=[MinValueValidator(MIN_SLOT_MINUTES), MaxValueValidator(MAX_SLOT_MINUTES)],
    )
    #: Bookings further ahead than this are refused. A clinic that lets
    #: somebody book in 2029 will have that booking outlive the doctor's
    #: employment, the price list and the phone number used to confirm it.
    booking_horizon_days = models.PositiveSmallIntegerField(default=120)
    accepts_walk_ins = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("clinic")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["branch", "name"], name="unique_clinic_name_per_branch")
        ]

    def __str__(self) -> str:
        return self.name


class DoctorSchedule(BranchScoped):
    """
    "Dr Ilesanmi runs the morning GOPD clinic on Tuesdays, 9 to 1, four an
    hour."

    One row per weekday rather than a date range with a recurrence rule. An
    RRULE is more expressive and nobody in a Nigerian hospital administration
    office is going to debug one; a weekday, two times and a validity window
    is what a rota actually is, and it is readable in the database.

    ``valid_from`` and ``valid_until`` are how a rota changes without
    destroying history: the old row is closed off and a new one starts. An
    appointment booked last month under the old rota stays explicable.
    """

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="schedules")
    staff = models.ForeignKey(
        "accounts.StaffMembership",
        on_delete=models.PROTECT,
        related_name="schedules",
        help_text=_("Who runs this session."),
    )

    weekday = models.IntegerField(choices=Weekday, db_index=True)
    starts_at = models.TimeField()
    ends_at = models.TimeField()

    slot_minutes = models.PositiveSmallIntegerField(
        default=15,
        validators=[MinValueValidator(MIN_SLOT_MINUTES), MaxValueValidator(MAX_SLOT_MINUTES)],
    )
    #: How many patients may be booked into one slot.
    #:
    #: Not always one. A consultant clinic that books four patients to the
    #: hour and sees them in order is how a great many Nigerian clinics
    #: genuinely run, and pretending otherwise produces a diary nobody uses
    #: and a paper book beside it.
    capacity_per_slot = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(20)]
    )

    valid_from = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("doctor schedule")
        ordering = ("weekday", "starts_at")
        indexes = [
            models.Index(fields=["organisation", "clinic", "weekday"]),
            models.Index(fields=["organisation", "staff", "weekday"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F("starts_at")),
                name="schedule_ends_after_it_starts",
            ),
            models.CheckConstraint(
                condition=models.Q(valid_until__isnull=True)
                | models.Q(valid_until__gte=models.F("valid_from")),
                name="schedule_validity_window_is_not_backwards",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_weekday_display()} {self.starts_at:%H:%M} to {self.ends_at:%H:%M}"

    def clean(self) -> None:
        super().clean()
        if self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": _("The session has to end after it starts.")})
        if self.slot_minutes and self.minutes_long % self.slot_minutes:
            # Refused rather than rounded: a 9-to-13 session with 25-minute
            # slots silently losing the last 15 minutes is the sort of thing
            # that is noticed six weeks later by a patient standing at a desk.
            raise ValidationError(
                {
                    "slot_minutes": _(
                        "The session is %(length)d minutes long, which does not divide into "
                        "%(slot)d-minute slots."
                    )
                    % {"length": self.minutes_long, "slot": self.slot_minutes}
                }
            )
        if self.valid_until and self.valid_until < self.valid_from:
            raise ValidationError({"valid_until": _("That is before the start date.")})

    @property
    def minutes_long(self) -> int:
        start = dt.datetime.combine(dt.date.min, self.starts_at)
        end = dt.datetime.combine(dt.date.min, self.ends_at)
        return int((end - start).total_seconds() // 60)

    @property
    def slots_per_session(self) -> int:
        return self.minutes_long // self.slot_minutes if self.slot_minutes else 0

    @property
    def places_per_session(self) -> int:
        return self.slots_per_session * self.capacity_per_slot

    def covers(self, day: dt.date) -> bool:
        """Is this rota row in force on that date?"""
        if not self.is_active or day.weekday() != self.weekday:
            return False
        if day < self.valid_from:
            return False
        return not (self.valid_until and day > self.valid_until)


class ScheduleException(BranchScoped):
    """
    A day, or a run of days, when somebody is not available.

    Leave, a public holiday, a conference, a theatre list that ate the
    clinic, the generator being down.

    **This removes availability. It does not cancel appointments.** Entering
    two weeks of annual leave over a clinic with fifty bookings must not
    quietly cancel fifty people who were given a date and have arranged a
    day off and transport. So the bookings stay, ``affected_appointments``
    counts them, and a human decides what to do with each one — move it, or
    ring the patient. The screen that creates an exception shows that number
    before it saves.
    """

    class Kind(models.TextChoices):
        LEAVE = "leave", _("Leave")
        PUBLIC_HOLIDAY = "public_holiday", _("Public holiday")
        CONFERENCE = "conference", _("Conference or training")
        CLOSURE = "closure", _("Clinic closed")
        OTHER = "other", _("Other")

    #: Null means the whole branch: a public holiday, or the clinic closing.
    staff = models.ForeignKey(
        "accounts.StaffMembership",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="schedule_exceptions",
    )
    #: Null means every clinic the person runs.
    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, null=True, blank=True, related_name="exceptions"
    )

    kind = models.CharField(max_length=20, choices=Kind, default=Kind.LEAVE)
    starts_on = models.DateField(db_index=True)
    ends_on = models.DateField(db_index=True, help_text=_("Inclusive."))
    reason = models.CharField(max_length=200, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta(BranchScoped.Meta):
        verbose_name = _("schedule exception")
        ordering = ("-starts_on",)
        indexes = [models.Index(fields=["organisation", "starts_on", "ends_on"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_on__gte=models.F("starts_on")),
                name="exception_ends_on_or_after_it_starts",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.starts_on} to {self.ends_on}"

    def clean(self) -> None:
        super().clean()
        if self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": _("That is before the start date.")})

    def covers(self, day: dt.date) -> bool:
        return self.starts_on <= day <= self.ends_on

    def applies_to(self, schedule: DoctorSchedule) -> bool:
        """
        Does this exception knock out that rota row?

        A branch-wide closure applies to everything. A person's leave applies
        to everything they run, unless it names one clinic.
        """
        if self.staff_id and self.staff_id != schedule.staff_id:
            return False
        return not (self.clinic_id and self.clinic_id != schedule.clinic_id)


class Appointment(BranchScoped):
    """
    A patient booked into a clinic at a time.

    ``starts_at`` is stored as an aware datetime in UTC like everything else
    and displayed in Africa/Lagos. Nigeria has no daylight saving, so none of
    the usual recurrence horrors apply — but the column is still aware,
    because a hospital group with a branch abroad is a thing that happens and
    naive datetimes are not retrofittable.
    """

    class Status(models.TextChoices):
        BOOKED = "booked", _("Booked")
        CHECKED_IN = "checked_in", _("Checked in")
        IN_PROGRESS = "in_progress", _("With the clinician")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")
        NO_SHOW = "no_show", _("Did not attend")

    #: Statuses that still occupy a place in a slot.
    LIVE_STATUSES = ("booked", "checked_in", "in_progress")

    class Source(models.TextChoices):
        FRONT_DESK = "front_desk", _("Front desk")
        PORTAL = "portal", _("Patient portal")
        PHONE = "phone", _("Telephone")
        CLINICIAN = "clinician", _("Booked by a clinician")
        WALK_IN = "walk_in", _("Walk-in")

    class Kind(models.TextChoices):
        IN_PERSON = "in_person", _("In person")
        TELEMEDICINE = "telemedicine", _("Video consultation")

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="appointments"
    )
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name="appointments")
    #: The rota row this was booked against. Kept so an appointment can
    #: still explain itself after the rota changes underneath it.
    schedule = models.ForeignKey(
        DoctorSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointments",
    )
    staff = models.ForeignKey(
        "accounts.StaffMembership",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="appointments",
        help_text=_("The clinician. Null means whoever is running the clinic."),
    )

    starts_at = models.DateTimeField(db_index=True)
    duration_minutes = models.PositiveSmallIntegerField(default=15)

    status = models.CharField(max_length=16, choices=Status, default=Status.BOOKED, db_index=True)
    source = models.CharField(max_length=16, choices=Source, default=Source.FRONT_DESK)
    kind = models.CharField(max_length=16, choices=Kind, default=Kind.IN_PERSON)
    reason = models.CharField(max_length=200, blank=True)

    booked_at = models.DateTimeField(default=timezone.now)
    booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    checked_in_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    cancellation_reason = models.CharField(max_length=200, blank=True)

    #: Where a rescheduled appointment went. The old row is cancelled rather
    #: than edited, so "this was moved twice" is visible on the timeline
    #: instead of being a single row that mysteriously says Thursday.
    rescheduled_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="rescheduled_from"
    )

    class Meta(BranchScoped.Meta):
        verbose_name = _("appointment")
        ordering = ("starts_at",)
        indexes = [
            models.Index(fields=["organisation", "starts_at", "status"]),
            models.Index(fields=["organisation", "clinic", "starts_at"]),
            models.Index(fields=["organisation", "patient", "-starts_at"]),
        ]
        constraints = [
            # One patient, one clinic, one moment — once.
            #
            # Partial, on the live statuses only, so a cancelled booking does
            # not block rebooking the same patient into the same slot, which
            # is exactly what happens when somebody cancels by mistake and
            # rings back five minutes later.
            models.UniqueConstraint(
                fields=["patient", "clinic", "starts_at"],
                condition=models.Q(status__in=("booked", "checked_in", "in_progress")),
                name="one_live_appointment_per_patient_slot",
            )
        ]

    def __str__(self) -> str:
        return f"{self.patient_id} at {self.starts_at:%Y-%m-%d %H:%M}"

    @property
    def is_live(self) -> bool:
        return self.status in self.LIVE_STATUSES

    @property
    def ends_at(self) -> dt.datetime:
        return self.starts_at + dt.timedelta(minutes=self.duration_minutes)

    def clean(self) -> None:
        super().clean()
        if self.duration_minutes < MIN_SLOT_MINUTES:
            raise ValidationError({"duration_minutes": _("That is too short to be a visit.")})


class QueueCounter(models.Model):
    """
    The next token number, per department per day.

    A row with a lock, for the same reason ``MedicalRecordCounter`` is one:
    two clerks checking in at the same moment must not both be handed A-019,
    because the number is called out loud across a waiting room.

    Reset daily. Nobody wants to be told they are number 4,812.
    """

    organisation = models.ForeignKey(
        "core.Organisation", on_delete=models.CASCADE, related_name="queue_counters"
    )
    branch = models.ForeignKey("core.Branch", on_delete=models.CASCADE, related_name="+")
    department = models.ForeignKey("core.Department", on_delete=models.CASCADE, related_name="+")
    day = models.DateField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("queue counter")
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "department", "day"], name="one_queue_counter_per_day"
            )
        ]

    def __str__(self) -> str:
        return f"{self.department_id} {self.day}: {self.last_number}"


class QueueTicket(BranchScoped):
    """
    A position in today's queue: "A-019".

    Issued at check-in, whether the patient had an appointment or walked in.
    This is the row the journey board and the waiting-room screen read, and
    it is deliberately thin — a token, a department, a state and the times
    it changed — because it is written to constantly and read by everybody.

    ``label`` is denormalised rather than rebuilt from ``number`` on every
    read: it is printed on a slip, shown on a television and spoken aloud,
    and all three must agree forever even if the department's code is
    renamed next year.
    """

    class Status(models.TextChoices):
        WAITING = "waiting", _("Waiting")
        CALLED = "called", _("Called")
        SERVING = "serving", _("Being seen")
        DONE = "done", _("Finished")
        SKIPPED = "skipped", _("Not present when called")
        LEFT = "left", _("Left without being seen")

    ACTIVE_STATUSES = ("waiting", "called", "serving")

    department = models.ForeignKey(
        "core.Department", on_delete=models.PROTECT, related_name="queue_tickets"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="queue_tickets"
    )
    appointment = models.OneToOneField(
        Appointment, on_delete=models.SET_NULL, null=True, blank=True, related_name="ticket"
    )

    day = models.DateField(default=timezone.localdate, db_index=True)
    number = models.PositiveIntegerField()
    label = models.CharField(max_length=16, help_text=_("A-019, as printed and called."))

    status = models.CharField(max_length=12, choices=Status, default=Status.WAITING, db_index=True)
    #: Walk-ins and booked patients queue together, in arrival order, with
    #: one exception: triage can raise this and a critical patient goes
    #: first. Lower sorts earlier.
    priority = models.SmallIntegerField(
        default=0, help_text=_("Lower is seen sooner. Triage sets this.")
    )

    issued_at = models.DateTimeField(default=timezone.now, db_index=True)
    called_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    #: Which desk or room called it, for the waiting-room screen.
    called_to = models.CharField(max_length=60, blank=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("queue ticket")
        ordering = ("priority", "issued_at")
        indexes = [
            models.Index(fields=["organisation", "day", "status"]),
            models.Index(fields=["branch", "department", "day", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "department", "day", "number"],
                name="one_ticket_number_per_department_per_day",
            )
        ]

    def __str__(self) -> str:
        return self.label

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    def minutes_waiting(self, now: dt.datetime | None = None) -> int:
        """
        How long they have been waiting, in whole minutes.

        Stops at the moment they were called, not at the moment they were
        seen: the wait a patient experiences ends when their number comes
        up, and measuring to the start of the consultation would flatter
        every number on the board.
        """
        end = self.called_at or now or timezone.now()
        return max(int((end - self.issued_at).total_seconds() // 60), 0)
