"""
What is free, and when.

Slots are **computed from the rota every time they are asked for**, never
stored. Three reasons, in the order they matter:

1. A stored slot goes stale. Change a Tuesday clinic from four an hour to
   three and every pre-generated Tuesday for the next year is wrong, in a
   table nobody thinks to migrate.
2. A year of empty slots for twenty doctors is about a million rows that are
   almost entirely "nothing is booked here", which is the one fact a
   database is best at inferring.
3. Computing it means availability and the booking check read the *same*
   rota. Two code paths that could disagree about whether 10:15 exists is
   how a patient ends up booked into a slot the diary does not show.

The cost is that booking has to re-derive the slot under a lock rather than
simply claiming a row. ``booking.py`` does exactly that, and the race is
covered by a test that books the same slot twice concurrently.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Count, Q
from django.utils import timezone

from apps.scheduling.models import (
    Appointment,
    Clinic,
    DoctorSchedule,
    ScheduleException,
)

#: Never compute more than this in one request. A client asking for a year
#: of a twenty-doctor clinic would otherwise build a quarter of a million
#: objects to answer "what is free next week".
MAX_DAYS = 62


@dataclass(frozen=True, slots=True)
class Slot:
    """One bookable moment, with how much room is left in it."""

    starts_at: dt.datetime
    minutes: int
    schedule_id: str
    staff_id: str
    staff_name: str
    capacity: int
    taken: int

    @property
    def free(self) -> int:
        return max(self.capacity - self.taken, 0)

    @property
    def is_free(self) -> bool:
        return self.free > 0


@dataclass(frozen=True, slots=True)
class DayAvailability:
    day: dt.date
    slots: tuple[Slot, ...]
    #: Why there is nothing, when there is nothing. The difference between
    #: "the doctor is on leave" and "this clinic does not run on Sundays"
    #: is the difference between a patient waiting and a patient going
    #: somewhere else, so the screen is given the reason rather than an
    #: empty list.
    closed_because: str = ""

    @property
    def free_count(self) -> int:
        return sum(slot.free for slot in self.slots)


def schedules_for(clinic: Clinic, day: dt.date) -> list[DoctorSchedule]:
    """The rota rows in force at that clinic on that date."""
    rows = DoctorSchedule.objects.filter(
        clinic=clinic, weekday=day.weekday(), is_active=True, valid_from__lte=day
    ).filter(Q(valid_until__isnull=True) | Q(valid_until__gte=day))
    return [row for row in rows.select_related("staff__user") if row.covers(day)]


def exceptions_for(clinic: Clinic, days: list[dt.date]) -> list[ScheduleException]:
    """
    Every exception touching that range, fetched once.

    One query for the whole window rather than one per day: a month view
    asks about thirty-one days and a hospital with a public holiday, two
    doctors on leave and a closure would otherwise make ninety-three round
    trips to draw one calendar.
    """
    if not days:
        return []
    return list(
        ScheduleException.objects.filter(
            branch_id=clinic.branch_id,
            starts_on__lte=max(days),
            ends_on__gte=min(days),
        ).filter(Q(clinic__isnull=True) | Q(clinic=clinic))
    )


def booked_counts(
    clinic: Clinic, start: dt.datetime, end: dt.datetime
) -> dict[tuple[str, dt.datetime], int]:
    """
    How many live appointments sit on each (schedule, start time).

    Cancelled and no-show bookings are not counted: the place they held is
    free again, which is the entire reason a cancellation is worth
    recording rather than deleting.
    """
    rows = (
        Appointment.objects.filter(
            clinic=clinic,
            starts_at__gte=start,
            starts_at__lt=end,
            status__in=Appointment.LIVE_STATUSES,
        )
        .values("schedule_id", "starts_at")
        .annotate(taken=Count("id"))
    )
    counts: dict[tuple[str, dt.datetime], int] = defaultdict(int)
    for row in rows:
        counts[(str(row["schedule_id"]), row["starts_at"])] += row["taken"]
    return counts


def availability(
    clinic: Clinic,
    *,
    start_day: dt.date,
    days: int = 7,
    staff_id: str | None = None,
    include_full: bool = False,
) -> list[DayAvailability]:
    """
    What is bookable at this clinic over a window of days.

    ``include_full`` keeps slots with no room left. The booking screen wants
    them — a diary that hides a full ten o'clock looks like a clinic that
    does not run at ten, and the clerk needs to see it is full in order to
    tell the patient so.

    Slots already in the past are dropped. Nobody books backwards, and
    showing this morning's empty nine o'clock as available is how a patient
    gets told to come at a time that has gone.
    """
    span = max(1, min(days, MAX_DAYS))
    window = [start_day + dt.timedelta(days=offset) for offset in range(span)]

    exceptions = exceptions_for(clinic, window)
    current_zone = timezone.get_current_timezone()
    now = timezone.now()

    range_start = timezone.make_aware(dt.datetime.combine(window[0], dt.time.min), current_zone)
    range_end = timezone.make_aware(
        dt.datetime.combine(window[-1] + dt.timedelta(days=1), dt.time.min), current_zone
    )
    taken = booked_counts(clinic, range_start, range_end)

    results: list[DayAvailability] = []
    for day in window:
        rota = schedules_for(clinic, day)
        if staff_id:
            rota = [row for row in rota if str(row.staff_id) == str(staff_id)]

        if not rota:
            results.append(
                DayAvailability(
                    day=day,
                    slots=(),
                    closed_because=f"This clinic does not run on {day:%A}s.",
                )
            )
            continue

        blocked = [
            exception
            for exception in exceptions
            if exception.covers(day) and any(exception.applies_to(row) for row in rota)
        ]
        running = [
            row
            for row in rota
            if not any(exception.covers(day) and exception.applies_to(row) for exception in blocked)
        ]
        if not running:
            reason = blocked[0]
            label = reason.get_kind_display()
            results.append(
                DayAvailability(
                    day=day,
                    slots=(),
                    closed_because=(f"{label}{': ' + reason.reason if reason.reason else ''}"),
                )
            )
            continue

        slots: list[Slot] = []
        #: Set when a slot was dropped only because it has already gone.
        #: "Everything is taken" and "the clinic has finished for today" are
        #: different answers, and a clerk reading the wrong one tells the
        #: patient to come back tomorrow when there is a free three o'clock.
        any_in_the_past = False
        for row in running:
            cursor = timezone.make_aware(dt.datetime.combine(day, row.starts_at), current_zone)
            session_end = timezone.make_aware(dt.datetime.combine(day, row.ends_at), current_zone)
            while cursor < session_end:
                used = taken.get((str(row.pk), cursor), 0)
                slot = Slot(
                    starts_at=cursor,
                    minutes=row.slot_minutes,
                    schedule_id=str(row.pk),
                    staff_id=str(row.staff_id),
                    staff_name=_staff_name(row),
                    capacity=row.capacity_per_slot,
                    taken=used,
                )
                if cursor < now:
                    any_in_the_past = True
                elif include_full or slot.is_free:
                    slots.append(slot)
                cursor += dt.timedelta(minutes=row.slot_minutes)

        slots.sort(key=lambda item: (item.starts_at, item.staff_name))
        # `closed`, not `reason`: that name is already bound to a
        # ScheduleException higher up in this loop.
        if slots:
            closed = ""
        elif any_in_the_past:
            closed = "This clinic has finished for today."
        else:
            closed = "Everything on this day is taken."
        results.append(DayAvailability(day=day, slots=tuple(slots), closed_because=closed))
    return results


def find_slot(clinic: Clinic, *, schedule: DoctorSchedule, starts_at: dt.datetime) -> Slot | None:
    """
    The one slot a booking is aiming at, or None if the rota does not have it.

    This is the check that stops a crafted request booking 03:00 at a clinic
    that runs nine to one. A client sends a time; the rota decides whether
    that time exists.
    """
    local_day = timezone.localtime(starts_at).date()
    if not schedule.covers(local_day) or schedule.clinic_id != clinic.pk:
        return None

    current_zone = timezone.get_current_timezone()
    cursor = timezone.make_aware(dt.datetime.combine(local_day, schedule.starts_at), current_zone)
    session_end = timezone.make_aware(
        dt.datetime.combine(local_day, schedule.ends_at), current_zone
    )
    while cursor < session_end:
        if cursor == starts_at:
            used = Appointment.objects.filter(
                schedule=schedule,
                starts_at=cursor,
                status__in=Appointment.LIVE_STATUSES,
            ).count()
            return Slot(
                starts_at=cursor,
                minutes=schedule.slot_minutes,
                schedule_id=str(schedule.pk),
                staff_id=str(schedule.staff_id),
                staff_name=_staff_name(schedule),
                capacity=schedule.capacity_per_slot,
                taken=used,
            )
        cursor += dt.timedelta(minutes=schedule.slot_minutes)
    return None


def blocking_exception(schedule: DoctorSchedule, day: dt.date) -> ScheduleException | None:
    """The leave or closure stopping that rota row on that day, if any."""
    for exception in ScheduleException.objects.filter(
        branch_id=schedule.branch_id, starts_on__lte=day, ends_on__gte=day
    ).filter(Q(clinic__isnull=True) | Q(clinic_id=schedule.clinic_id)):
        if exception.applies_to(schedule):
            return exception
    return None


def affected_appointments(exception: ScheduleException):
    """
    The bookings an exception would strand.

    Shown before the exception is saved and listed afterwards, because
    entering leave never cancels anybody — see ``ScheduleException``. The
    answer to "what happens to these fifty people" is a person ringing them,
    and this is the list they work from.
    """
    current_zone = timezone.get_current_timezone()
    start = timezone.make_aware(dt.datetime.combine(exception.starts_on, dt.time.min), current_zone)
    end = timezone.make_aware(
        dt.datetime.combine(exception.ends_on + dt.timedelta(days=1), dt.time.min), current_zone
    )
    rows = Appointment.objects.filter(
        branch_id=exception.branch_id,
        starts_at__gte=start,
        starts_at__lt=end,
        status__in=Appointment.LIVE_STATUSES,
    )
    if exception.clinic_id:
        rows = rows.filter(clinic_id=exception.clinic_id)
    if exception.staff_id:
        rows = rows.filter(staff_id=exception.staff_id)
    return rows.select_related("patient", "clinic").order_by("starts_at")


def _staff_name(schedule: DoctorSchedule) -> str:
    user = getattr(schedule.staff, "user", None)
    if user is None:  # pragma: no cover - staff is non-null
        return ""
    return user.get_full_name() or user.email


__all__ = [
    "MAX_DAYS",
    "DayAvailability",
    "Slot",
    "affected_appointments",
    "availability",
    "blocking_exception",
    "find_slot",
    "schedules_for",
]
