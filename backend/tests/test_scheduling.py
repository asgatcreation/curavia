"""
Appointments, rotas, leave and token numbers.

Every name here is synthetic.

The weight of this file is on three things that are easy to get wrong and
expensive when they are:

* **Two people in one place.** Slots are computed rather than stored, so
  there is no unique index doing the work and the guard is explicit. There
  is a test that books the same slot from two connections at once.
* **Leave that cancels nobody.** Entering a doctor's annual leave must
  remove availability and leave the existing bookings alone, with a list of
  who is affected.
* **Token numbers that are never issued twice**, because the number is
  called out loud across a waiting room.
"""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connections
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.patients.models import Patient, Sex
from apps.patients.registration import RegistrationInput, register_patient
from apps.scheduling import availability as avail
from apps.scheduling import booking
from apps.scheduling.models import (
    Appointment,
    Clinic,
    DoctorSchedule,
    QueueTicket,
    ScheduleException,
    Weekday,
)

pytestmark = [pytest.mark.django_db]


# --------------------------------------------------------------------------
# Scaffolding
# --------------------------------------------------------------------------


def next_weekday(weekday: int, *, after: dt.date | None = None) -> dt.date:
    """The next date that falls on that weekday, never today."""
    day = (after or timezone.localdate()) + dt.timedelta(days=1)
    while day.weekday() != weekday:
        day += dt.timedelta(days=1)
    return day


def at(day: dt.date, hour: int, minute: int = 0):
    return timezone.make_aware(
        dt.datetime.combine(day, dt.time(hour, minute)), timezone.get_current_timezone()
    )


@pytest.fixture
def clinic(hospital) -> Clinic:
    with hospital.scope():
        department = Department.objects.filter(kind=Department.Kind.CLINIC).first()
        assert department is not None, "the hospital factory should seed a clinic department"
        return Clinic.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            department=department,
            name="General Outpatient — Morning",
            default_duration_minutes=15,
        )


@pytest.fixture
def doctor(hospital):
    return hospital.staff("doctor")


@pytest.fixture
def schedule(hospital, clinic, doctor) -> DoctorSchedule:
    """Tuesdays, 9 to 1, four an hour, one patient per slot."""
    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        return DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=membership,
            weekday=Weekday.TUESDAY,
            starts_at=dt.time(9, 0),
            ends_at=dt.time(13, 0),
            slot_minutes=15,
            capacity_per_slot=1,
        )


def make_patient(hospital, **overrides) -> Patient:
    defaults = {
        "first_name": "Chiamaka",
        "last_name": "Eze",
        "sex": Sex.FEMALE,
        "phone": "08031234567",
        "date_of_birth": dt.date(1992, 3, 2),
    }
    defaults.update(overrides)
    with hospital.scope():
        return register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(**defaults),
        )


@pytest.fixture
def api_as_desk(api, hospital):
    user = hospital.staff("front_desk")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    return api


# --------------------------------------------------------------------------
# The rota
# --------------------------------------------------------------------------


def test_a_session_that_does_not_divide_into_slots_is_refused(hospital, clinic, doctor) -> None:
    """
    Nine to one with 25-minute slots loses the last fifteen minutes.

    Rounding silently is how a clinic ends up with a slot that the diary
    shows and the doctor has gone home from.
    """
    from django.core.exceptions import ValidationError

    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        row = DoctorSchedule(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=membership,
            weekday=Weekday.TUESDAY,
            starts_at=dt.time(9, 0),
            ends_at=dt.time(13, 0),
            slot_minutes=25,
        )
        with pytest.raises(ValidationError, match="does not divide"):
            row.full_clean(exclude=["organisation", "branch"])


def test_a_session_computes_its_own_size(schedule) -> None:
    assert schedule.minutes_long == 240
    assert schedule.slots_per_session == 16
    assert schedule.places_per_session == 16


def test_a_rota_row_only_covers_its_own_weekday_and_window(schedule) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    assert schedule.covers(tuesday)
    assert not schedule.covers(tuesday + dt.timedelta(days=1))

    schedule.valid_until = tuesday - dt.timedelta(days=1)
    assert not schedule.covers(tuesday), "a closed-off rota row still offered slots"


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------


def test_availability_offers_the_whole_session(hospital, clinic, schedule) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    with hospital.scope():
        days = avail.availability(clinic, start_day=tuesday, days=1)

    assert len(days) == 1
    assert len(days[0].slots) == 16
    assert days[0].slots[0].starts_at == at(tuesday, 9, 0)
    assert days[0].slots[-1].starts_at == at(tuesday, 12, 45)


def test_a_day_the_clinic_does_not_run_says_so_rather_than_being_empty(
    hospital, clinic, schedule
) -> None:
    """
    An empty list and "closed on Sundays" look the same to a client and
    mean different things to a patient.
    """
    wednesday = next_weekday(Weekday.WEDNESDAY)
    with hospital.scope():
        days = avail.availability(clinic, start_day=wednesday, days=1)

    assert days[0].slots == ()
    assert "does not run on Wednesday" in days[0].closed_because


def test_a_booked_slot_drops_out_of_availability(hospital, clinic, schedule) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        days = avail.availability(clinic, start_day=tuesday, days=1)

    starts = [slot.starts_at for slot in days[0].slots]
    assert at(tuesday, 9, 0) not in starts
    assert len(starts) == 15


def test_a_full_slot_can_be_shown_as_full_rather_than_hidden(hospital, clinic, schedule) -> None:
    """A diary that hides a full ten o'clock looks like a clinic that has no ten o'clock."""
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        days = avail.availability(clinic, start_day=tuesday, days=1, include_full=True)

    nine = next(slot for slot in days[0].slots if slot.starts_at == at(tuesday, 9, 0))
    assert nine.taken == 1
    assert nine.free == 0
    assert not nine.is_free


def test_a_cancelled_booking_frees_its_place_again(hospital, clinic, schedule) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        appointment = booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        booking.cancel(appointment, reason="Patient rang to cancel.")
        days = avail.availability(clinic, start_day=tuesday, days=1)

    assert at(tuesday, 9, 0) in [slot.starts_at for slot in days[0].slots]


def test_a_clinic_with_capacity_four_offers_four_places_in_one_slot(
    hospital, clinic, schedule
) -> None:
    """
    Four to the hour, seen in order, is how a great many Nigerian consultant
    clinics genuinely run.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    with hospital.scope():
        DoctorSchedule.objects.filter(pk=schedule.pk).update(capacity_per_slot=4)
        schedule.refresh_from_db()
        for index in range(3):
            booking.book(
                booking.BookingRequest(
                    patient=make_patient(hospital, last_name=f"Eze{index}"),
                    clinic=clinic,
                    schedule=schedule,
                    starts_at=at(tuesday, 9, 0),
                )
            )
        days = avail.availability(clinic, start_day=tuesday, days=1, include_full=True)

    nine = next(slot for slot in days[0].slots if slot.starts_at == at(tuesday, 9, 0))
    assert nine.taken == 3
    assert nine.free == 1


def test_slots_in_the_past_are_not_offered(hospital, clinic, doctor) -> None:
    """Showing this morning's empty nine o'clock sends a patient to a time that has gone."""
    today = timezone.localdate()
    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=membership,
            weekday=today.weekday(),
            starts_at=dt.time(0, 0),
            ends_at=dt.time(23, 45),
            slot_minutes=15,
        )
        days = avail.availability(clinic, start_day=today, days=1)

    now = timezone.now()
    assert all(slot.starts_at >= now for slot in days[0].slots)


def test_availability_cannot_be_asked_for_a_year(hospital, clinic, schedule) -> None:
    """A client asking for 400 days would build a quarter of a million objects."""
    with hospital.scope():
        days = avail.availability(clinic, start_day=timezone.localdate(), days=400)
    assert len(days) == avail.MAX_DAYS


# --------------------------------------------------------------------------
# Booking
# --------------------------------------------------------------------------


def test_booking_refuses_a_time_the_clinic_does_not_run(hospital, clinic, schedule) -> None:
    """
    The check that stops a crafted request booking three in the morning.

    A client sends a time; the rota decides whether that time exists.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope(), pytest.raises(booking.BookingError, match="not a time this clinic runs"):
        booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 3, 0)
            )
        )


def test_booking_refuses_a_time_between_slots(hospital, clinic, schedule) -> None:
    """09:07 is inside the session and is not a slot."""
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope(), pytest.raises(booking.BookingError, match="not a time this clinic runs"):
        booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 7)
            )
        )


def test_booking_refuses_the_past(hospital, clinic, doctor) -> None:
    yesterday = timezone.localdate() - dt.timedelta(days=1)
    patient = make_patient(hospital)
    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        row = DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=membership,
            weekday=yesterday.weekday(),
            starts_at=dt.time(9, 0),
            ends_at=dt.time(13, 0),
            slot_minutes=15,
            valid_from=yesterday,
        )
        with pytest.raises(booking.BookingError, match="already passed"):
            booking.book(
                booking.BookingRequest(
                    patient=patient, clinic=clinic, schedule=row, starts_at=at(yesterday, 9, 0)
                )
            )


def test_booking_refuses_beyond_the_horizon(hospital, clinic, schedule) -> None:
    """
    A booking in 2029 outlives the doctor's employment and the price list.
    """
    with hospital.scope():
        Clinic.objects.filter(pk=clinic.pk).update(booking_horizon_days=7)
        clinic.refresh_from_db()
    far = next_weekday(Weekday.TUESDAY, after=timezone.localdate() + dt.timedelta(days=30))
    patient = make_patient(hospital)
    with hospital.scope(), pytest.raises(booking.BookingError, match="days ahead"):
        booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(far, 9, 0)
            )
        )


def test_a_second_booking_into_a_full_slot_is_refused(hospital, clinic, schedule) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    with hospital.scope():
        booking.book(
            booking.BookingRequest(
                patient=make_patient(hospital),
                clinic=clinic,
                schedule=schedule,
                starts_at=at(tuesday, 10, 0),
            )
        )
        with pytest.raises(booking.BookingError, match="is full"):
            booking.book(
                booking.BookingRequest(
                    patient=make_patient(hospital, last_name="Adeyemi"),
                    clinic=clinic,
                    schedule=schedule,
                    starts_at=at(tuesday, 10, 0),
                )
            )


def test_a_merged_away_record_cannot_be_booked(hospital, clinic, schedule) -> None:
    """Booking the dead half of a split record is how somebody is expected at a clinic nobody can find."""
    from apps.patients.merge import merge_patients

    tuesday = next_weekday(Weekday.TUESDAY)
    keeper = make_patient(hospital)
    duplicate = make_patient(hospital, last_name="Ezeh")
    with hospital.scope():
        merge_patients(winner=keeper, loser=duplicate, reason="same person")
        duplicate.refresh_from_db()
        with pytest.raises(booking.BookingError, match="merged into another"):
            booking.book(
                booking.BookingRequest(
                    patient=duplicate,
                    clinic=clinic,
                    schedule=schedule,
                    starts_at=at(tuesday, 9, 0),
                )
            )


def test_a_patient_cannot_be_in_two_places_at_once(hospital, clinic, schedule, doctor) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        other = Clinic.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            department=clinic.department,
            name="Afternoon review",
        )
        other_schedule = DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=other,
            staff=membership,
            weekday=Weekday.TUESDAY,
            starts_at=dt.time(9, 0),
            ends_at=dt.time(13, 0),
            slot_minutes=15,
        )
        booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        with pytest.raises(booking.BookingError, match="already has an appointment"):
            booking.book(
                booking.BookingRequest(
                    patient=patient,
                    clinic=other,
                    schedule=other_schedule,
                    starts_at=at(tuesday, 9, 0),
                )
            )


@pytest.mark.django_db(transaction=True)
def test_two_clerks_booking_the_same_slot_at_once_produce_one_appointment(
    hospital, clinic, schedule
) -> None:
    """
    The race the whole of booking.py exists to lose safely.

    Slots are computed, so there is no row to claim and no unique index to
    rely on. Both threads re-derive the slot inside a lock on the rota row;
    the second one sees the first one's appointment and is refused.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    first = make_patient(hospital, last_name="Okafor")
    second = make_patient(hospital, last_name="Balogun")
    when = at(tuesday, 11, 0)

    def attempt(patient_id):
        try:
            with hospital.scope():
                patient = Patient.objects.get(pk=patient_id)
                booking.book(
                    booking.BookingRequest(
                        patient=patient,
                        clinic=clinic,
                        schedule=schedule,
                        starts_at=when,
                    )
                )
            return "booked"
        except booking.BookingError as exc:
            return str(exc)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [first.pk, second.pk]))

    assert outcomes.count("booked") == 1, outcomes
    assert any("full" in outcome for outcome in outcomes), outcomes

    with hospital.scope():
        live = Appointment.objects.filter(
            starts_at=when, status__in=Appointment.LIVE_STATUSES
        ).count()
    assert live == 1


# --------------------------------------------------------------------------
# Moving and cancelling
# --------------------------------------------------------------------------


def test_moving_an_appointment_leaves_both_rows_behind(hospital, clinic, schedule) -> None:
    """
    A move is a cancellation plus a booking, not an edit.

    An appointment moved twice should read as three rows on the timeline,
    not one row that mysteriously says Thursday.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        original = booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        moved = booking.reschedule(
            original, schedule=schedule, starts_at=at(tuesday, 11, 30), reason="Patient asked."
        )
        original.refresh_from_db()

    assert original.status == Appointment.Status.CANCELLED
    assert original.rescheduled_to_id == moved.pk
    assert moved.starts_at == at(tuesday, 11, 30)


def test_a_failed_move_leaves_the_original_booking_alone(hospital, clinic, schedule) -> None:
    """
    The one that would be a disaster: cancel first, then fail to rebook, and
    the patient has no appointment at all.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    blocker = make_patient(hospital, last_name="Bello")
    with hospital.scope():
        original = booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        booking.book(
            booking.BookingRequest(
                patient=blocker, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 11, 30)
            )
        )
        with pytest.raises(booking.BookingError, match="is full"):
            booking.reschedule(original, schedule=schedule, starts_at=at(tuesday, 11, 30))
        original.refresh_from_db()

    assert original.status == Appointment.Status.BOOKED, "the original was lost to a failed move"


def test_a_cancellation_is_kept_rather_than_deleted(hospital, clinic, schedule) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        appointment = booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        booking.cancel(appointment, reason="Patient is travelling.")
        appointment.refresh_from_db()

    assert appointment.status == Appointment.Status.CANCELLED
    assert appointment.cancellation_reason == "Patient is travelling."
    assert appointment.cancelled_at is not None


def test_a_cancelled_slot_can_be_rebooked_by_the_same_patient(hospital, clinic, schedule) -> None:
    """Somebody cancels by mistake and rings back five minutes later."""
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        first = booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        booking.cancel(first, reason="Pressed the wrong button.")
        again = booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )

    assert again.pk != first.pk
    assert again.status == Appointment.Status.BOOKED


# --------------------------------------------------------------------------
# Leave
# --------------------------------------------------------------------------


def test_leave_removes_availability(hospital, clinic, schedule, doctor) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        ScheduleException.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            staff=membership,
            kind=ScheduleException.Kind.LEAVE,
            starts_on=tuesday,
            ends_on=tuesday + dt.timedelta(days=13),
            reason="Annual leave",
        )
        days = avail.availability(clinic, start_day=tuesday, days=1)

    assert days[0].slots == ()
    assert "Leave" in days[0].closed_because
    assert "Annual leave" in days[0].closed_because


def test_leave_does_not_cancel_a_single_appointment(hospital, clinic, schedule, doctor) -> None:
    """
    The decision this module is built around.

    Fifty people were given a date and have arranged a day off and transport
    from another state. Software does not cancel them on its own; it hands
    somebody the list.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        appointment = booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        membership = doctor.memberships.get(organisation=hospital.organisation)
        exception = ScheduleException.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            staff=membership,
            starts_on=tuesday,
            ends_on=tuesday,
            reason="Called to theatre",
        )
        stranded = list(avail.affected_appointments(exception))
        appointment.refresh_from_db()

    assert appointment.status == Appointment.Status.BOOKED, "leave cancelled somebody"
    assert [row.pk for row in stranded] == [appointment.pk]


def test_booking_into_leave_is_refused_with_the_reason(hospital, clinic, schedule, doctor) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        ScheduleException.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            staff=membership,
            kind=ScheduleException.Kind.CONFERENCE,
            starts_on=tuesday,
            ends_on=tuesday,
        )
        with pytest.raises(booking.BookingError, match="conference or training"):
            booking.book(
                booking.BookingRequest(
                    patient=patient,
                    clinic=clinic,
                    schedule=schedule,
                    starts_at=at(tuesday, 9, 0),
                )
            )


def test_a_branch_wide_closure_applies_to_everybody(hospital, clinic, schedule) -> None:
    """A public holiday names no doctor and closes everything."""
    tuesday = next_weekday(Weekday.TUESDAY)
    with hospital.scope():
        ScheduleException.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            kind=ScheduleException.Kind.PUBLIC_HOLIDAY,
            starts_on=tuesday,
            ends_on=tuesday,
            reason="Eid",
        )
        days = avail.availability(clinic, start_day=tuesday, days=1)

    assert days[0].slots == ()
    assert "Public holiday" in days[0].closed_because


def test_one_doctors_leave_leaves_another_doctors_clinic_running(
    hospital, clinic, schedule
) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    other = hospital.staff("consultant", email="locum@lifeline.test")
    with hospital.scope():
        membership = other.memberships.get(organisation=hospital.organisation)
        DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=membership,
            weekday=Weekday.TUESDAY,
            starts_at=dt.time(9, 0),
            ends_at=dt.time(11, 0),
            slot_minutes=30,
        )
        ScheduleException.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            staff=schedule.staff,
            starts_on=tuesday,
            ends_on=tuesday,
        )
        days = avail.availability(clinic, start_day=tuesday, days=1)

    assert days[0].slots, "one doctor's leave closed the whole clinic"
    assert {slot.staff_id for slot in days[0].slots} == {str(membership.pk)}


# --------------------------------------------------------------------------
# Token numbers
# --------------------------------------------------------------------------


def test_a_token_carries_the_department_letter_and_resets_daily(hospital, clinic) -> None:
    patient = make_patient(hospital)
    with hospital.scope():
        ticket = booking.check_in(
            patient=patient, department=clinic.department, branch=hospital.main_branch
        )

    letter = clinic.department.code[0].upper()
    assert ticket.label == f"{letter}-001"
    assert ticket.number == 1


def test_tokens_count_up_within_a_department(hospital, clinic) -> None:
    with hospital.scope():
        labels = [
            booking.check_in(
                patient=make_patient(hospital, last_name=f"Eze{index}"),
                department=clinic.department,
                branch=hospital.main_branch,
            ).label
            for index in range(3)
        ]

    assert [label[-3:] for label in labels] == ["001", "002", "003"]


@pytest.mark.django_db(transaction=True)
def test_two_clerks_checking_in_at_once_never_share_a_number(hospital, clinic) -> None:
    """
    The number is called out loud across a waiting room. Two people holding
    A-019 is a scene at the desk, not a database anomaly.
    """
    patients = [make_patient(hospital, last_name=f"Sanusi{index}") for index in range(6)]

    def arrive(patient_id):
        try:
            with hospital.scope():
                patient = Patient.objects.get(pk=patient_id)
                return booking.check_in(
                    patient=patient,
                    department=clinic.department,
                    branch=hospital.main_branch,
                ).label
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=6) as pool:
        labels = list(pool.map(arrive, [patient.pk for patient in patients]))

    assert len(set(labels)) == len(labels), f"a token was issued twice: {labels}"


def test_checking_in_twice_hands_back_the_same_number(hospital, clinic) -> None:
    """
    A clerk pressing check-in twice must not send the patient to the back of
    a queue they are already in.
    """
    patient = make_patient(hospital)
    with hospital.scope():
        first = booking.check_in(
            patient=patient, department=clinic.department, branch=hospital.main_branch
        )
        second = booking.check_in(
            patient=patient, department=clinic.department, branch=hospital.main_branch
        )

    assert first.pk == second.pk


def test_checking_in_a_booked_patient_marks_the_appointment(hospital, clinic, schedule) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        appointment = booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        ticket = booking.check_in(
            patient=patient,
            department=clinic.department,
            branch=hospital.main_branch,
            appointment=appointment,
        )
        appointment.refresh_from_db()

    assert appointment.status == Appointment.Status.CHECKED_IN
    assert appointment.checked_in_at is not None
    assert ticket.appointment_id == appointment.pk


def test_the_queue_is_called_in_arrival_order(hospital, clinic) -> None:
    with hospital.scope():
        first = booking.check_in(
            patient=make_patient(hospital, last_name="Early"),
            department=clinic.department,
            branch=hospital.main_branch,
        )
        booking.check_in(
            patient=make_patient(hospital, last_name="Later"),
            department=clinic.department,
            branch=hospital.main_branch,
        )
        called = booking.call_next(
            department=clinic.department, branch=hospital.main_branch, called_to="Room 2"
        )

    assert called is not None
    assert called.pk == first.pk
    assert called.called_to == "Room 2"
    assert called.status == QueueTicket.Status.CALLED


def test_triage_can_move_a_sick_patient_up_the_queue(hospital, clinic) -> None:
    """The one exception to arrival order, and it is visible on the board."""
    with hospital.scope():
        booking.check_in(
            patient=make_patient(hospital, last_name="Waiting"),
            department=clinic.department,
            branch=hospital.main_branch,
        )
        urgent = booking.check_in(
            patient=make_patient(hospital, last_name="Collapsed"),
            department=clinic.department,
            branch=hospital.main_branch,
            priority=-3,
        )
        called = booking.call_next(department=clinic.department, branch=hospital.main_branch)

    assert called is not None
    assert called.pk == urgent.pk


def test_a_skipped_patient_goes_back_into_the_queue_not_out_of_it(hospital, clinic) -> None:
    """
    Somebody who missed their number because they were in the toilet has not
    left. Sending them to the back of a four-hour queue is a punishment the
    software should not hand out.
    """
    with hospital.scope():
        ticket = booking.check_in(
            patient=make_patient(hospital),
            department=clinic.department,
            branch=hospital.main_branch,
        )
        booking.call_next(department=clinic.department, branch=hospital.main_branch)
        ticket.refresh_from_db()
        booking.set_ticket_status(ticket, QueueTicket.Status.SKIPPED)
        ticket.refresh_from_db()

    assert ticket.status == QueueTicket.Status.WAITING
    assert ticket.priority == 1, "a skipped patient should be seen after those who were present"


def test_finishing_a_ticket_completes_its_appointment(hospital, clinic, schedule) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        appointment = booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        ticket = booking.check_in(
            patient=patient,
            department=clinic.department,
            branch=hospital.main_branch,
            appointment=appointment,
        )
        booking.set_ticket_status(ticket, QueueTicket.Status.DONE)
        appointment.refresh_from_db()

    assert appointment.status == Appointment.Status.COMPLETED


def test_the_wait_is_measured_to_being_called_not_to_being_seen(hospital, clinic) -> None:
    """Measuring to the start of the consultation would flatter every number on the board."""
    with hospital.scope():
        ticket = booking.check_in(
            patient=make_patient(hospital),
            department=clinic.department,
            branch=hospital.main_branch,
        )
        QueueTicket.objects.filter(pk=ticket.pk).update(
            issued_at=timezone.now() - dt.timedelta(minutes=70),
            called_at=timezone.now() - dt.timedelta(minutes=10),
        )
        ticket.refresh_from_db()

    assert ticket.minutes_waiting() == 60


def test_no_shows_are_marked_only_after_the_grace_period(hospital, clinic, doctor) -> None:
    """
    A patient forty minutes late in Lagos traffic is a patient who arrived.
    The number has to mean "did not come", not "was late".
    """
    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        today = timezone.localdate()
        row = DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=membership,
            weekday=today.weekday(),
            starts_at=dt.time(0, 0),
            ends_at=dt.time(23, 45),
            slot_minutes=15,
        )
        late = Appointment.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            patient=make_patient(hospital, last_name="Late"),
            clinic=clinic,
            schedule=row,
            starts_at=timezone.now() - dt.timedelta(minutes=30),
        )
        absent = Appointment.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            patient=make_patient(hospital, last_name="Absent"),
            clinic=clinic,
            schedule=row,
            starts_at=timezone.now() - dt.timedelta(minutes=180),
        )
        booking.mark_no_shows(branch_id=hospital.main_branch.pk)
        late.refresh_from_db()
        absent.refresh_from_db()

    assert late.status == Appointment.Status.BOOKED
    assert absent.status == Appointment.Status.NO_SHOW


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------


def test_the_desk_can_see_availability_and_book(api_as_desk, hospital, clinic, schedule) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)

    free = api_as_desk.get(
        reverse("api:v1:availability"),
        {"clinic": str(clinic.pk), "from": tuesday.isoformat(), "days": 1},
    )
    assert free.status_code == 200, free.data
    assert free.data["days"][0]["free_count"] == 16
    slot = free.data["days"][0]["slots"][0]

    booked = api_as_desk.post(
        reverse("api:v1:appointment-book"),
        {
            "patient_id": str(patient.pk),
            "clinic_id": str(clinic.pk),
            "schedule_id": slot["schedule_id"],
            "starts_at": slot["starts_at"],
            "reason": "Headaches for two weeks",
        },
        format="json",
    )
    assert booked.status_code == 201, booked.data
    assert booked.data["patient_mrn"] == patient.mrn
    assert booked.data["status"] == "booked"


def test_booking_a_taken_slot_comes_back_as_a_conflict(
    api_as_desk, hospital, clinic, schedule
) -> None:
    """
    409, not 400: nothing the client sent was malformed. Somebody else took
    the slot between the diary being drawn and Book being pressed.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    with hospital.scope():
        booking.book(
            booking.BookingRequest(
                patient=make_patient(hospital, last_name="First"),
                clinic=clinic,
                schedule=schedule,
                starts_at=at(tuesday, 9, 0),
            )
        )
    patient = make_patient(hospital, last_name="Second")
    response = api_as_desk.post(
        reverse("api:v1:appointment-book"),
        {
            "patient_id": str(patient.pk),
            "clinic_id": str(clinic.pk),
            "schedule_id": str(schedule.pk),
            "starts_at": at(tuesday, 9, 0).isoformat(),
        },
        format="json",
    )
    assert response.status_code == 409
    assert "full" in response.data["detail"]


def test_a_doctor_may_book_a_follow_up_but_not_cancel(api, hospital, clinic, schedule) -> None:
    """
    appointment.book and appointment.cancel are deliberately different
    permissions. See docs/permissions-matrix.yml.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    api.force_authenticate(user=hospital.staff("doctor", email="dr2@lifeline.test"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )

    booked = api.post(
        reverse("api:v1:appointment-book"),
        {
            "patient_id": str(patient.pk),
            "clinic_id": str(clinic.pk),
            "schedule_id": str(schedule.pk),
            "starts_at": at(tuesday, 9, 0).isoformat(),
        },
        format="json",
    )
    assert booked.status_code == 201, booked.data

    refused = api.post(
        reverse("api:v1:appointment-action", args=[booked.data["id"], "cancel"]),
        {"reason": "Changed my mind."},
        format="json",
    )
    assert refused.status_code == 403


def test_a_cashier_cannot_read_the_diary(api, hospital, clinic) -> None:
    """The diary is a list of named people and why they are coming."""
    api.force_authenticate(user=hospital.staff("cashier"))
    api.credentials(HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk))
    response = api.get(reverse("api:v1:appointment-list"))
    assert response.status_code == 403


def test_reading_a_clinic_day_is_audited_as_a_bulk_read(
    api_as_desk, hospital, clinic, schedule
) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    with hospital.scope():
        booking.book(
            booking.BookingRequest(
                patient=make_patient(hospital),
                clinic=clinic,
                schedule=schedule,
                starts_at=at(tuesday, 9, 0),
            )
        )
    api_as_desk.get(reverse("api:v1:appointment-list"), {"day": tuesday.isoformat()})

    event = AuditEvent.objects.filter(action="appointment.diary.view").first()
    assert event is not None
    assert "bulk" in event.flags


def test_entering_leave_previews_who_it_strands_before_saving(
    api, hospital, clinic, schedule, doctor
) -> None:
    """
    The preview is the point: twelve names with phone numbers is a list
    somebody works through, where "12 affected" is a number they nod at.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    patient = make_patient(hospital)
    with hospital.scope():
        booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, 9, 0)
            )
        )
        membership = doctor.memberships.get(organisation=hospital.organisation)

    api.force_authenticate(user=hospital.staff("facility_admin"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    body = {
        "staff": str(membership.pk),
        "kind": "leave",
        "starts_on": tuesday.isoformat(),
        "ends_on": tuesday.isoformat(),
        "reason": "Annual leave",
    }

    preview = api.post(reverse("api:v1:schedule-exception") + "?preview=true", body, format="json")
    assert preview.status_code == 200, preview.data
    assert preview.data["affected_count"] == 1
    assert preview.data["affected"][0]["patient_mrn"] == patient.mrn

    with hospital.scope():
        assert not ScheduleException.objects.exists(), "a preview saved something"

    created = api.post(reverse("api:v1:schedule-exception"), body, format="json")
    assert created.status_code == 201
    with hospital.scope():
        assert ScheduleException.objects.count() == 1


def test_the_front_desk_cannot_enter_leave(api_as_desk, hospital) -> None:
    """Rotas reach every booking made after them. Administration only."""
    tuesday = next_weekday(Weekday.TUESDAY)
    response = api_as_desk.post(
        reverse("api:v1:schedule-exception"),
        {"kind": "closure", "starts_on": tuesday.isoformat(), "ends_on": tuesday.isoformat()},
        format="json",
    )
    assert response.status_code == 403


def test_the_queue_endpoints_check_in_call_and_finish(api_as_desk, hospital, clinic) -> None:
    patient = make_patient(hospital)

    checked_in = api_as_desk.post(
        reverse("api:v1:queue-check-in"),
        {"patient_id": str(patient.pk), "department_id": str(clinic.department_id)},
        format="json",
    )
    assert checked_in.status_code == 201, checked_in.data
    assert checked_in.data["label"].endswith("001")
    assert checked_in.data["status"] == "waiting"

    queue = api_as_desk.get(reverse("api:v1:queue"), {"department": str(clinic.department_id)})
    assert [row["id"] for row in queue.data] == [checked_in.data["id"]]

    called = api_as_desk.post(
        reverse("api:v1:queue-call-next"),
        {"department_id": str(clinic.department_id), "called_to": "Room 1"},
        format="json",
    )
    assert called.data["status"] == "called"
    assert called.data["called_to"] == "Room 1"

    done = api_as_desk.post(
        reverse("api:v1:queue-ticket-action", args=[called.data["id"], "done"]), format="json"
    )
    assert done.data["status"] == "done"

    after = api_as_desk.get(reverse("api:v1:queue"), {"department": str(clinic.department_id)})
    assert after.data == []


def test_checking_in_is_audited_with_the_token(api_as_desk, hospital, clinic) -> None:
    patient = make_patient(hospital)
    response = api_as_desk.post(
        reverse("api:v1:queue-check-in"),
        {"patient_id": str(patient.pk), "department_id": str(clinic.department_id)},
        format="json",
    )
    event = AuditEvent.objects.filter(action="queue.check_in").first()
    assert event is not None
    assert event.changes["token"] == response.data["label"]
    assert event.patient_id == patient.pk


def test_scheduling_is_scoped_to_one_hospital(hospital, other_hospital, clinic, schedule) -> None:
    with other_hospital.scope():
        assert Clinic.objects.count() == 0
        assert DoctorSchedule.objects.count() == 0
    with hospital.scope():
        assert Clinic.objects.count() == 1


def test_an_unknown_ticket_action_is_refused_in_plain_words(api_as_desk, hospital, clinic) -> None:
    patient = make_patient(hospital)
    ticket = api_as_desk.post(
        reverse("api:v1:queue-check-in"),
        {"patient_id": str(patient.pk), "department_id": str(clinic.department_id)},
        format="json",
    )
    response = api_as_desk.post(
        reverse("api:v1:queue-ticket-action", args=[ticket.data["id"], "teleport"]), format="json"
    )
    assert response.status_code == 400
    assert response.data["code"] == "unknown_action"


# --------------------------------------------------------------------------
# Permissions that arrive after a hospital does
# --------------------------------------------------------------------------


def test_an_owner_created_before_a_permission_existed_still_gets_it(hospital) -> None:
    """
    Found by walking the app: the appointment permissions were added in this
    milestone, and the hospital already in the development database had an
    owner role seeded without them — so the owner got 403 on their own
    clinic list.

    Roles are per-hospital copies, which is right for every role except the
    owner's: `role_templates.py` generates that one rather than listing it,
    exactly so "there is one role that can do anything" stays true. Without
    the top-up that promise would hold only on the day each hospital signed
    up. See apps/accounts/role_sync.py.
    """
    from apps.accounts.models import Role
    from apps.accounts.role_sync import sync_owner_roles
    from apps.accounts.role_templates import all_hospital_codes

    with hospital.scope():
        owner = Role.objects.get(slug="owner")
        # Wind the role back to before this milestone.
        owner.permissions = sorted(set(owner.permissions) - {"appointment.book", "queue.manage"})
        owner.save(update_fields=["permissions"])

        assert sync_owner_roles() == 1
        owner.refresh_from_db()

    assert "appointment.book" in owner.permissions
    assert "queue.manage" in owner.permissions
    assert set(owner.permissions) == set(all_hospital_codes())


def test_the_top_up_never_takes_a_permission_away(hospital) -> None:
    """
    A hospital that has genuinely edited its owner role keeps what it put
    there. This only ever adds.
    """
    from apps.accounts.models import Role
    from apps.accounts.role_sync import sync_owner_roles

    with hospital.scope():
        owner = Role.objects.get(slug="owner")
        owner.permissions = [*owner.permissions, "something.a.hospital.invented"]
        owner.save(update_fields=["permissions"])

        sync_owner_roles()
        owner.refresh_from_db()

    assert "something.a.hospital.invented" in owner.permissions


def test_the_top_up_leaves_every_other_role_alone(hospital) -> None:
    """
    A hospital that narrowed its nurse role must not have it widened again
    by a deployment.
    """
    from apps.accounts.models import Role
    from apps.accounts.role_sync import sync_owner_roles

    with hospital.scope():
        nurse = Role.objects.get(slug="nurse")
        nurse.permissions = ["patients.view"]
        nurse.save(update_fields=["permissions"])

        sync_owner_roles()
        nurse.refresh_from_db()

    assert nurse.permissions == ["patients.view"]


def test_the_top_up_does_nothing_when_there_is_nothing_to_add(hospital) -> None:
    """Idempotent, so it can run on every deploy without a migration."""
    from apps.accounts.role_sync import sync_owner_roles

    with hospital.scope():
        assert sync_owner_roles() == 0


def test_a_finished_clinic_says_so_rather_than_claiming_to_be_full(
    hospital, clinic, doctor
) -> None:
    """
    Found by walking the app at five in the afternoon: today's session had
    ended and the panel said "Everything on this day is taken", which sends
    a clerk looking for tomorrow when there is a free three o'clock.
    """
    today = timezone.localdate()
    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=membership,
            weekday=today.weekday(),
            starts_at=dt.time(0, 0),
            ends_at=dt.time(0, 15),
            slot_minutes=15,
        )
        days = avail.availability(clinic, start_day=today, days=1)

    assert days[0].slots == ()
    assert days[0].closed_because == "This clinic has finished for today."


def test_a_genuinely_full_day_still_says_it_is_full(hospital, clinic, doctor) -> None:
    tuesday = next_weekday(Weekday.TUESDAY)
    with hospital.scope():
        membership = doctor.memberships.get(organisation=hospital.organisation)
        row = DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=membership,
            weekday=Weekday.TUESDAY,
            starts_at=dt.time(14, 0),
            ends_at=dt.time(14, 15),
            slot_minutes=15,
        )
        booking.book(
            booking.BookingRequest(
                patient=make_patient(hospital),
                clinic=clinic,
                schedule=row,
                starts_at=at(tuesday, 14, 0),
            )
        )
        days = avail.availability(clinic, start_day=tuesday, days=1)

    assert days[0].closed_because == "Everything on this day is taken."
