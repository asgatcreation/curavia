"""
Booking, moving and cancelling an appointment.

**The whole file exists to stop two people being given the same place.**

Slots are computed rather than stored (see ``availability.py``), so there is
no row to claim and no unique index that can do the work. The guard is
therefore explicit: take a lock on the rota row, re-derive the slot *inside*
the lock, count what is already booked, and only then write. Two clerks
pressing Book at the same instant serialise on that lock, and the second one
sees the first one's appointment.

Locking the schedule rather than the appointments is deliberate. There is
nothing to lock in an empty slot — the row that would conflict does not
exist yet — which is the classic phantom read, and the only thing both
transactions can agree to queue behind is the rota row they are both
reading from.

The cost is that bookings into one doctor's session serialise. That is the
correct trade: a doctor's clinic takes bookings at the rate a human types,
and a double-booked patient costs an hour of somebody's morning.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.patients.models import Patient
from apps.scheduling import availability as avail
from apps.scheduling.models import (
    Appointment,
    Clinic,
    DoctorSchedule,
    QueueCounter,
    QueueTicket,
)


class BookingError(Exception):
    """The booking was refused. The message is shown to the clerk."""


@dataclass(frozen=True, slots=True)
class BookingRequest:
    patient: Patient
    clinic: Clinic
    schedule: DoctorSchedule
    starts_at: dt.datetime
    reason: str = ""
    source: str = Appointment.Source.FRONT_DESK
    kind: str = Appointment.Kind.IN_PERSON


@transaction.atomic
def book(request: BookingRequest, *, booked_by=None) -> Appointment:
    """
    Put a patient in a slot, or refuse with a reason a clerk can act on.

    Every refusal below says what is wrong in words, because the person
    reading it has a patient in front of them and needs to know whether to
    offer a different time, a different doctor, or a different day.
    """
    # The lock. Everything after this line is inside it, including the
    # re-derivation of the slot and the count of what is already booked.
    schedule = DoctorSchedule.objects.select_for_update().get(pk=request.schedule.pk)
    clinic = request.clinic
    starts_at = request.starts_at

    if not clinic.is_active:
        raise BookingError("That clinic is not running.")
    if request.patient.merged_into_id is not None:
        raise BookingError(
            "That record has been merged into another one. Book on the surviving record."
        )
    if request.patient.is_deceased:
        raise BookingError("That patient is recorded as deceased.")

    now = timezone.now()
    if starts_at < now:
        raise BookingError("That time has already passed.")

    horizon = now + dt.timedelta(days=clinic.booking_horizon_days)
    if starts_at > horizon:
        raise BookingError(
            f"This clinic only takes bookings {clinic.booking_horizon_days} days ahead."
        )

    local_day = timezone.localtime(starts_at).date()
    blocked = avail.blocking_exception(schedule, local_day)
    if blocked is not None:
        label = blocked.get_kind_display().lower()
        raise BookingError(f"Nobody is running that clinic on {local_day:%A %d %B} ({label}).")

    slot = avail.find_slot(clinic, schedule=schedule, starts_at=starts_at)
    if slot is None:
        raise BookingError("That is not a time this clinic runs.")
    if not slot.is_free:
        raise BookingError(
            f"{timezone.localtime(starts_at):%H:%M} is full "
            f"({slot.capacity} already booked). Try another time."
        )

    clash = Appointment.objects.filter(
        patient=request.patient,
        starts_at=starts_at,
        status__in=Appointment.LIVE_STATUSES,
    ).first()
    if clash is not None:
        raise BookingError("This patient already has an appointment at that time.")

    appointment = Appointment(
        organisation_id=clinic.organisation_id,
        branch_id=clinic.branch_id,
        patient=request.patient,
        clinic=clinic,
        schedule=schedule,
        staff=schedule.staff,
        starts_at=starts_at,
        duration_minutes=schedule.slot_minutes,
        source=request.source,
        kind=request.kind,
        reason=request.reason.strip()[:200],
        booked_by=booked_by,
    )
    appointment.full_clean(exclude=["organisation", "branch"])
    appointment.save()
    return appointment


@transaction.atomic
def cancel(appointment: Appointment, *, reason: str, cancelled_by=None) -> Appointment:
    """
    Cancel, keeping the row.

    Never deleted. A cancellation is a fact about what happened — it is what
    a no-show rate is measured against, and it is what tells the next clerk
    that this patient has cancelled three times — and a deleted row says
    only that nothing ever happened.
    """
    if appointment.status in (Appointment.Status.CANCELLED, Appointment.Status.COMPLETED):
        raise BookingError("That appointment is already closed.")

    appointment.status = Appointment.Status.CANCELLED
    appointment.cancelled_at = timezone.now()
    appointment.cancelled_by = cancelled_by
    appointment.cancellation_reason = reason.strip()[:200]
    appointment.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancellation_reason",
            "updated_at",
        ]
    )
    return appointment


@transaction.atomic
def reschedule(
    appointment: Appointment,
    *,
    schedule: DoctorSchedule,
    starts_at: dt.datetime,
    reason: str = "",
    actor=None,
) -> Appointment:
    """
    Move an appointment by cancelling the old one and booking a new one.

    Not an edit of ``starts_at``. Two reasons, and the second is the real
    one: the new time has to go through the same slot guard as any other
    booking, and an appointment that was moved twice should read as three
    rows on the patient's timeline rather than one row that mysteriously
    says Thursday.
    """
    if not appointment.is_live:
        raise BookingError("That appointment is no longer open, so it cannot be moved.")

    moved = book(
        BookingRequest(
            patient=appointment.patient,
            clinic=appointment.clinic,
            schedule=schedule,
            starts_at=starts_at,
            reason=appointment.reason,
            source=appointment.source,
            kind=appointment.kind,
        ),
        booked_by=actor,
    )
    cancel(
        appointment,
        reason=reason.strip() or "Moved to another time.",
        cancelled_by=actor,
    )
    Appointment.objects.filter(pk=appointment.pk).update(rescheduled_to=moved)
    appointment.refresh_from_db()
    return moved


# ---------------------------------------------------------------------------
# Arriving
# ---------------------------------------------------------------------------


def next_token(
    *, organisation_id, branch_id, department, day: dt.date | None = None
) -> tuple[int, str]:
    """
    Issue the next token number for a department today: ``(19, "A-019")``.

    Row-locked for the same reason an MRN is: the number is called out loud
    across a waiting room, and two people holding A-019 is a scene at the
    desk rather than a database anomaly.

    The letter comes from the department code, so GOPD is G and Antenatal is
    A — which is what the paper slips in these clinics already do.
    """
    day = day or timezone.localdate()
    with transaction.atomic():
        counter, _created = QueueCounter.objects.select_for_update().get_or_create(
            organisation_id=organisation_id,
            branch_id=branch_id,
            department=department,
            day=day,
        )
        counter.last_number += 1
        counter.save(update_fields=["last_number"])
        letter = (department.code or department.name or "Q").strip()[:1].upper() or "Q"
        return counter.last_number, f"{letter}-{counter.last_number:03d}"


@transaction.atomic
def check_in(
    *,
    patient: Patient,
    department,
    branch,
    appointment: Appointment | None = None,
    priority: int = 0,
) -> QueueTicket:
    """
    Put somebody in today's queue and hand them a number.

    The same function for a booked patient and a walk-in, because they join
    the same queue and the desk does the same thing for both. An appointment
    changes only where the ticket came from, never how it is treated — a
    hospital where booked patients are a different kind of object is a
    hospital where the walk-in, who is most of the day, is the unhandled
    case.
    """
    if patient.merged_into_id is not None:
        raise BookingError(
            "That record has been merged into another one. Check in on the surviving record."
        )

    today = timezone.localdate()
    existing = QueueTicket.objects.filter(
        patient=patient,
        department=department,
        day=today,
        status__in=QueueTicket.ACTIVE_STATUSES,
    ).first()
    if existing is not None:
        # Not an error. A clerk pressing check-in twice should get the
        # number the patient is already holding, not a second one that puts
        # them at the back of a queue they are already in.
        return existing

    if appointment is not None:
        if not appointment.is_live:
            raise BookingError("That appointment is not open.")
        if appointment.patient_id != patient.pk:
            raise BookingError("That appointment belongs to a different patient.")

    number, label = next_token(
        organisation_id=patient.organisation_id, branch_id=branch.pk, department=department
    )
    ticket = QueueTicket.objects.create(
        organisation_id=patient.organisation_id,
        branch=branch,
        department=department,
        patient=patient,
        appointment=appointment,
        day=today,
        number=number,
        label=label,
        priority=priority,
    )

    if appointment is not None:
        appointment.status = Appointment.Status.CHECKED_IN
        appointment.checked_in_at = timezone.now()
        appointment.save(update_fields=["status", "checked_in_at", "updated_at"])

    # Arriving at the desk is the start of a visit, so the encounter opens
    # here rather than being a separate thing somebody has to remember. The
    # import is local because apps.journey reads scheduling's models, and
    # at module level the two would import each other.
    from apps.journey import service as journey

    journey.start_encounter(
        patient=patient,
        branch=branch,
        department=department,
        appointment=appointment,
        ticket=ticket,
        reason=appointment.reason if appointment is not None else "",
    )
    journey.announce_queue_change(patient.organisation_id, branch.pk)
    return ticket


@transaction.atomic
def call_next(*, department, branch, called_to: str = "") -> QueueTicket | None:
    """
    Call the next patient, and say where to.

    Ordered by priority then arrival, which is the only ordering a waiting
    room accepts: people can see who came in before them, and a queue that
    reorders itself invisibly is a queue people argue with the clerk about.
    Triage raising somebody's priority is the one exception, and it is
    visible on the board.

    Locks the row it picks, so two consulting rooms pressing "next" at the
    same moment do not both call the same person.
    """
    today = timezone.localdate()
    ticket = (
        QueueTicket.objects.select_for_update(skip_locked=True)
        .filter(
            department=department,
            branch=branch,
            day=today,
            status=QueueTicket.Status.WAITING,
        )
        .order_by("priority", "issued_at")
        .first()
    )
    if ticket is None:
        return None

    ticket.status = QueueTicket.Status.CALLED
    ticket.called_at = timezone.now()
    ticket.called_to = called_to.strip()[:60]
    ticket.save(update_fields=["status", "called_at", "called_to", "updated_at"])

    from apps.journey import service as journey

    journey.announce_queue_change(ticket.organisation_id, branch.pk)
    return ticket


@transaction.atomic
def set_ticket_status(ticket: QueueTicket, status: str) -> QueueTicket:
    """
    Move a ticket along, stamping whichever time that transition records.

    A ticket that is skipped goes back to waiting rather than out of the
    queue: somebody who missed their number because they were in the toilet
    has not left, and sending them to the back of a four-hour queue for it
    is a punishment the software should not be handing out. The desk can
    mark them as having left, explicitly, if they really have.
    """
    now = timezone.now()
    fields = ["status", "updated_at"]

    if status == QueueTicket.Status.SERVING:
        ticket.started_at = ticket.started_at or now
        fields.append("started_at")
    elif status in (QueueTicket.Status.DONE, QueueTicket.Status.LEFT):
        ticket.finished_at = now
        fields.append("finished_at")
        if ticket.appointment_id and status == QueueTicket.Status.DONE:
            Appointment.objects.filter(
                pk=ticket.appointment_id, status__in=Appointment.LIVE_STATUSES
            ).update(status=Appointment.Status.COMPLETED)
    elif status == QueueTicket.Status.SKIPPED:
        # Skipped means "called and not present", and the next call should
        # still reach them. Priority is nudged back by one so they are seen
        # after the people who were there, not instead of them.
        ticket.priority += 1
        ticket.status = QueueTicket.Status.WAITING
        ticket.save(update_fields=["status", "priority", "updated_at"])
        return ticket

    ticket.status = status
    ticket.save(update_fields=fields)

    from apps.journey import service as journey

    journey.announce_queue_change(ticket.organisation_id, ticket.branch_id)
    return ticket


def mark_no_shows(*, branch_id, grace_minutes: int = 60) -> int:
    """
    Close off bookings whose time has passed and who never checked in.

    Run by a scheduled job at the end of a clinic session rather than
    continuously, with an hour's grace, because a patient who arrives forty
    minutes late in Lagos traffic is a patient who arrived. The number is
    what a hospital uses to decide whether to overbook, so it has to mean
    "did not come", not "was late".
    """
    cutoff = timezone.now() - dt.timedelta(minutes=grace_minutes)
    return Appointment.objects.filter(
        branch_id=branch_id,
        starts_at__lt=cutoff,
        status=Appointment.Status.BOOKED,
    ).update(status=Appointment.Status.NO_SHOW, updated_at=timezone.now())


__all__ = [
    "BookingError",
    "BookingRequest",
    "book",
    "call_next",
    "cancel",
    "check_in",
    "mark_no_shows",
    "next_token",
    "reschedule",
    "set_ticket_status",
]
