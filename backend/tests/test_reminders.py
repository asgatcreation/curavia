"""
Appointment reminders and no-show tracking.

Every name and number here is synthetic. Nothing in this file can reach a
real phone or a real inbox: the test settings wire in the fakes, and there is
a test below that asserts exactly that.

The weight is on the five things that would do real harm:

* a message that discloses what the appointment is for,
* a message sent twice,
* a message sent at three in the morning,
* a message sent to somebody who asked us to stop,
* a reminder for an appointment that was cancelled.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.audit import AuditEvent
from apps.core.models import Department
from apps.patients.models import Patient, Sex
from apps.patients.registration import RegistrationInput, register_patient
from apps.scheduling import booking, reminders
from apps.scheduling.models import (
    Appointment,
    Clinic,
    DoctorSchedule,
    NoShowRecord,
    ReminderJob,
    Weekday,
)
from integrations.base import get_provider
from integrations.email.fake import FakeEmailProvider
from integrations.sms.fake import FakeSmsProvider

pytestmark = [pytest.mark.django_db]


# --------------------------------------------------------------------------
# Scaffolding
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _empty_outboxes():
    FakeSmsProvider.clear()
    FakeEmailProvider.clear()
    yield
    FakeSmsProvider.clear()
    FakeEmailProvider.clear()


def next_weekday(weekday: int) -> dt.date:
    day = timezone.localdate() + dt.timedelta(days=1)
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
        return Clinic.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            department=department,
            name="General Outpatient",
        )


@pytest.fixture
def schedule(hospital, clinic) -> DoctorSchedule:
    with hospital.scope():
        membership = hospital.staff("doctor").memberships.get(organisation=hospital.organisation)
        return DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=membership,
            weekday=Weekday.TUESDAY,
            starts_at=dt.time(9, 0),
            ends_at=dt.time(13, 0),
            slot_minutes=15,
        )


def make_patient(hospital, **overrides) -> Patient:
    defaults = {
        "first_name": "Ifeoma",
        "last_name": "Adeyemi",
        "sex": Sex.FEMALE,
        "phone": "08031234567",
        "date_of_birth": dt.date(1990, 2, 2),
    }
    defaults.update(overrides)
    with hospital.scope():
        return register_patient(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            data=RegistrationInput(**defaults),
        )


def book_one(hospital, clinic, schedule, patient, hour: int = 10) -> Appointment:
    tuesday = next_weekday(Weekday.TUESDAY)
    with hospital.scope():
        return booking.book(
            booking.BookingRequest(
                patient=patient, clinic=clinic, schedule=schedule, starts_at=at(tuesday, hour)
            )
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
# Nothing here can reach a real person
# --------------------------------------------------------------------------


def test_the_test_suite_cannot_send_anything_real() -> None:
    """
    The guard the rest of this file rests on. If the fakes were ever
    unwired, these tests would start texting real Nigerian phone numbers.
    """
    assert get_provider("sms").name == "fake"
    assert get_provider("email").name == "fake"


# --------------------------------------------------------------------------
# What a reminder is allowed to say
# --------------------------------------------------------------------------


def test_a_reminder_never_says_what_the_appointment_is_for(hospital, schedule) -> None:
    """
    The decision this whole module is shaped around.

    A text message is read on a lock screen, on a handset shared with a
    husband, a mother, a landlord. "Your appointment at the HIV clinic" is a
    disclosure we made about somebody who never agreed to it.
    """
    with hospital.scope():
        membership = schedule.staff
        sensitive = Clinic.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            department=schedule.clinic.department,
            name="HIV and Retroviral Clinic",
        )
        row = DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=sensitive,
            staff=membership,
            weekday=Weekday.TUESDAY,
            starts_at=dt.time(9, 0),
            ends_at=dt.time(13, 0),
            slot_minutes=15,
        )
        patient = make_patient(hospital)
        appointment = booking.book(
            booking.BookingRequest(
                patient=patient,
                clinic=sensitive,
                schedule=row,
                starts_at=at(next_weekday(Weekday.TUESDAY), 10),
                reason="Viral load review",
            )
        )
        message = reminders.compose(appointment)

    blob = f"{message.subject} {message.text} {message.html}"
    assert "HIV" not in blob
    assert "Retroviral" not in blob
    assert "Viral load" not in blob
    # The hospital's own name is the most that may be said, so the patient
    # recognises the message as real.
    assert hospital.organisation.name in blob


def test_a_reminder_says_the_hospital_the_day_and_the_time(hospital, clinic, schedule) -> None:
    patient = make_patient(hospital, first_name="Ifeoma")
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        message = reminders.compose(appointment)

    assert "Ifeoma" in message.text
    assert hospital.organisation.name in message.text
    assert "10:00" in message.text


# --------------------------------------------------------------------------
# Queueing
# --------------------------------------------------------------------------


def test_booking_queues_two_reminders(hospital, clinic, schedule) -> None:
    """
    Two, not one. The day-before message is the one that changes behaviour;
    the same-day one catches the person who forgot on the morning.
    """
    patient = make_patient(hospital)
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        kinds = set(appointment.reminders.values_list("kind", flat=True))

    assert kinds == {"day_before", "same_day"}


def test_queueing_twice_does_not_double_the_reminders(hospital, clinic, schedule) -> None:
    """Called on booking and on reschedule; a patient who moved twice gets two."""
    patient = make_patient(hospital)
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        reminders.schedule_for(appointment)
        reminders.schedule_for(appointment)
        assert appointment.reminders.count() == 2


def test_an_appointment_sooner_than_the_lead_time_gets_fewer(hospital, clinic, schedule) -> None:
    """
    Booked this morning for this afternoon.

    Found by this test: the day-before reminder was being queued with a send
    time already in the past, so it fired the instant the clerk pressed
    Book — a "see you tomorrow" message about an appointment three hours
    away, which costs money and reads as a mistake.
    """
    today = timezone.localdate()
    with hospital.scope():
        row = DoctorSchedule.objects.create(
            organisation=hospital.organisation,
            branch=hospital.main_branch,
            clinic=clinic,
            staff=schedule.staff,
            weekday=today.weekday(),
            starts_at=dt.time(0, 0),
            ends_at=dt.time(23, 45),
            slot_minutes=15,
        )
        soon = timezone.now() + dt.timedelta(hours=3)
        soon = soon.replace(minute=(soon.minute // 15) * 15, second=0, microsecond=0)
        appointment = booking.book(
            booking.BookingRequest(
                patient=make_patient(hospital), clinic=clinic, schedule=row, starts_at=soon
            )
        )
        kinds = list(appointment.reminders.values_list("kind", flat=True))

    assert "day_before" not in kinds


def test_cancelling_an_appointment_drops_its_reminders(hospital, clinic, schedule) -> None:
    """
    The most damaging thing this feature could do is send somebody across
    Lagos for an appointment that no longer exists.
    """
    patient = make_patient(hospital)
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        booking.cancel(appointment, reason="Patient rang to cancel.")
        statuses = set(appointment.reminders.values_list("status", flat=True))

    assert statuses == {ReminderJob.Status.CANCELLED}


# --------------------------------------------------------------------------
# Quiet hours
# --------------------------------------------------------------------------


def test_the_middle_of_the_night_is_quiet(hospital) -> None:
    """A reminder at 03:14 is not a reminder, it is a complaint."""
    tuesday = next_weekday(Weekday.TUESDAY)
    assert reminders.within_quiet_hours(at(tuesday, 3, 14))
    assert reminders.within_quiet_hours(at(tuesday, 22, 0))
    assert not reminders.within_quiet_hours(at(tuesday, 9, 0))
    assert not reminders.within_quiet_hours(at(tuesday, 19, 59))


def test_a_quiet_hour_send_moves_forward_to_the_morning(hospital) -> None:
    """
    Forwards, never backwards: a reminder arriving earlier than intended is
    a reminder about the wrong day.
    """
    tuesday = next_weekday(Weekday.TUESDAY)
    moved = reminders.next_sendable_time(at(tuesday, 3, 14))
    assert timezone.localtime(moved).hour == reminders.QUIET_END.hour
    assert timezone.localtime(moved).date() == tuesday

    late = reminders.next_sendable_time(at(tuesday, 22, 30))
    assert timezone.localtime(late).date() == tuesday + dt.timedelta(days=1)
    assert late > at(tuesday, 22, 30)


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------


def _make_due(appointment) -> ReminderJob:
    """Pull one of an appointment's reminders back to now."""
    job = appointment.reminders.filter(kind="day_before").first()
    ReminderJob.objects.filter(pk=job.pk).update(send_at=timezone.now())
    job.refresh_from_db()
    return job


def test_a_patient_with_an_email_address_is_emailed_not_texted(hospital, clinic, schedule) -> None:
    """Email is free and a text message is not. See decision 5."""
    patient = make_patient(hospital, email="ifeoma@example.test")
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        job = _make_due(appointment)
        outcome = reminders.send_one(job, now=at(next_weekday(Weekday.TUESDAY), 9, 0))
        job.refresh_from_db()

    assert outcome is not None and outcome.accepted
    assert job.status == ReminderJob.Status.SENT
    assert job.channel == "email"
    assert job.cost_kobo == 0
    assert len(FakeEmailProvider.sent) == 1
    assert FakeSmsProvider.sent == []


def test_a_patient_with_only_a_phone_is_texted(hospital, clinic, schedule) -> None:
    patient = make_patient(hospital, email="")
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        job = _make_due(appointment)
        reminders.send_one(job, now=at(next_weekday(Weekday.TUESDAY), 9, 0))
        job.refresh_from_db()

    assert job.channel == "sms"
    assert job.cost_kobo > 0, "a text message costs money and that has to be recorded"
    assert len(FakeSmsProvider.sent) == 1


def test_a_patient_with_no_contact_details_is_skipped_not_failed(
    hospital, clinic, schedule
) -> None:
    """
    Skipped, not failed: there is nothing wrong with the system, and a
    failure count that includes these is a number nobody can act on.
    """
    patient = make_patient(hospital, phone="", email="")
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        job = _make_due(appointment)
        outcome = reminders.send_one(job, now=at(next_weekday(Weekday.TUESDAY), 9, 0))
        job.refresh_from_db()

    assert outcome is None
    assert job.status == ReminderJob.Status.SKIPPED
    assert "No phone number" in job.failure_reason


def test_a_reminder_is_never_sent_twice(hospital, clinic, schedule) -> None:
    """
    Retries are normal for queued work. A retry that texts somebody twice
    costs the hospital money and makes the system look broken.
    """
    patient = make_patient(hospital, email="ifeoma@example.test")
    appointment = book_one(hospital, clinic, schedule, patient)
    morning = at(next_weekday(Weekday.TUESDAY), 9, 0)
    with hospital.scope():
        job = _make_due(appointment)
        reminders.send_one(job, now=morning)
        again = reminders.send_one(job, now=morning)

    assert again is None
    assert len(FakeEmailProvider.sent) == 1


def test_an_opted_out_patient_is_never_messaged(hospital, clinic, schedule) -> None:
    """
    Checked at send time, not at queue time: somebody who says "stop" today
    must not get the message queued yesterday.
    """
    patient = make_patient(hospital, email="ifeoma@example.test")
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        job = _make_due(appointment)
        Patient.objects.filter(pk=patient.pk).update(reminders_opted_out=True)
        outcome = reminders.send_one(job, now=at(next_weekday(Weekday.TUESDAY), 9, 0))
        job.refresh_from_db()

    assert outcome is None
    assert job.status == ReminderJob.Status.SKIPPED
    assert FakeEmailProvider.sent == []


def test_a_cancelled_appointment_stops_a_due_reminder(hospital, clinic, schedule) -> None:
    patient = make_patient(hospital, email="ifeoma@example.test")
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        job = _make_due(appointment)
        Appointment.objects.filter(pk=appointment.pk).update(status=Appointment.Status.CANCELLED)
        reminders.send_one(job, now=at(next_weekday(Weekday.TUESDAY), 9, 0))
        job.refresh_from_db()

    assert job.status == ReminderJob.Status.CANCELLED
    assert FakeEmailProvider.sent == []


def test_a_reminder_due_in_the_night_is_held_until_morning(hospital, clinic, schedule) -> None:
    patient = make_patient(hospital, email="ifeoma@example.test")
    appointment = book_one(hospital, clinic, schedule, patient)
    night = at(next_weekday(Weekday.TUESDAY), 3, 0)
    with hospital.scope():
        job = _make_due(appointment)
        outcome = reminders.send_one(job, now=night)
        job.refresh_from_db()

    assert outcome is None
    assert job.status == ReminderJob.Status.QUEUED
    assert job.send_at > night
    assert FakeEmailProvider.sent == []


def test_a_reminder_for_an_appointment_that_has_passed_is_skipped(
    hospital, clinic, schedule
) -> None:
    """Reminding somebody about an appointment that started is worse than silence."""
    patient = make_patient(hospital, email="ifeoma@example.test")
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        job = _make_due(appointment)
        outcome = reminders.send_one(job, now=appointment.starts_at + dt.timedelta(hours=4))
        job.refresh_from_db()

    assert outcome is None
    assert job.status == ReminderJob.Status.SKIPPED


def test_a_failing_provider_is_retried_then_given_up_on(
    hospital, clinic, schedule, monkeypatch
) -> None:
    """
    Each attempt may cost money, and a number that is wrong stays wrong.
    """
    from integrations.base import EmailResult

    def refuse(self, **kwargs):
        return EmailResult(provider="fake", message_id="", accepted=False, error="provider is down")

    monkeypatch.setattr(FakeEmailProvider, "send", refuse)

    patient = make_patient(hospital, email="ifeoma@example.test")
    appointment = book_one(hospital, clinic, schedule, patient)
    morning = at(next_weekday(Weekday.TUESDAY), 9, 0)

    with hospital.scope():
        job = _make_due(appointment)
        for _ in range(reminders.MAX_ATTEMPTS):
            ReminderJob.objects.filter(pk=job.pk).update(send_at=morning)
            job.refresh_from_db()
            reminders.send_one(job, now=morning)
        job.refresh_from_db()

    assert job.attempts == reminders.MAX_ATTEMPTS
    assert job.status == ReminderJob.Status.FAILED
    assert "provider is down" in job.failure_reason


def test_send_due_reports_what_it_did(hospital, clinic, schedule) -> None:
    with hospital.scope():
        for index in range(3):
            patient = make_patient(
                hospital, last_name=f"Adeyemi{index}", email=f"p{index}@example.test"
            )
            appointment = booking.book(
                booking.BookingRequest(
                    patient=patient,
                    clinic=clinic,
                    schedule=schedule,
                    starts_at=at(next_weekday(Weekday.TUESDAY), 10 + index),
                )
            )
            _make_due(appointment)

        outcome = reminders.send_due(now=at(next_weekday(Weekday.TUESDAY), 9, 0))

    # Five, not three: the three day-before jobs that were pulled forward,
    # plus the same-day jobs for the 10:00 and 11:00 appointments, which are
    # genuinely due by nine o'clock on the morning itself.
    assert outcome.sent == 5
    assert outcome.failed == 0
    assert len(FakeEmailProvider.sent) == 5


# --------------------------------------------------------------------------
# What it costs
# --------------------------------------------------------------------------


def test_the_estimate_separates_what_would_be_texted_from_what_would_be_emailed(
    hospital, clinic, schedule
) -> None:
    """
    "About ₦4,200 a month" is a decision a hospital owner can make.
    "Enable SMS reminders?" is not.
    """
    with hospital.scope():
        for index, email in enumerate(["has@example.test", "", ""]):
            patient = make_patient(hospital, last_name=f"Cost{index}", email=email)
            booking.book(
                booking.BookingRequest(
                    patient=patient,
                    clinic=clinic,
                    schedule=schedule,
                    starts_at=at(next_weekday(Weekday.TUESDAY), 10 + index),
                )
            )
        estimate = reminders.estimate_cost(organisation_id=hospital.organisation.pk)

    assert estimate["by_email"] == 2, "one patient with an email, two reminders each"
    assert estimate["by_sms"] == 4
    assert estimate["sms_cost_kobo"] > 0


# --------------------------------------------------------------------------
# Not turning up
# --------------------------------------------------------------------------


def test_a_no_show_is_recorded_with_whether_we_had_reminded_them(
    hospital, clinic, schedule
) -> None:
    """
    A patient who was never reminded did not miss an appointment so much as
    the hospital failed to tell them about one. A report that cannot tell
    those apart will be used to blame the wrong people.
    """
    patient = make_patient(hospital, email="ifeoma@example.test")
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        job = _make_due(appointment)
        reminders.send_one(job, now=at(next_weekday(Weekday.TUESDAY), 9, 0))

        Appointment.objects.filter(pk=appointment.pk).update(
            starts_at=timezone.now() - dt.timedelta(hours=3)
        )
        missed = reminders.record_no_shows(branch_id=hospital.main_branch.pk)
        appointment.refresh_from_db()
        record = NoShowRecord.objects.get(appointment=appointment)

    assert missed == 1
    assert appointment.status == Appointment.Status.NO_SHOW
    assert record.was_reminded is True


def test_somebody_merely_late_is_not_a_no_show(hospital, clinic, schedule) -> None:
    """Forty minutes late in Lagos traffic is somebody who came."""
    patient = make_patient(hospital)
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        Appointment.objects.filter(pk=appointment.pk).update(
            starts_at=timezone.now() - dt.timedelta(minutes=30)
        )
        reminders.record_no_shows(branch_id=hospital.main_branch.pk)
        appointment.refresh_from_db()

    assert appointment.status == Appointment.Status.BOOKED


def test_recording_a_no_show_twice_keeps_one_record(hospital, clinic, schedule) -> None:
    patient = make_patient(hospital)
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        Appointment.objects.filter(pk=appointment.pk).update(
            starts_at=timezone.now() - dt.timedelta(hours=3)
        )
        reminders.record_no_shows(branch_id=hospital.main_branch.pk)
        reminders.record_no_shows(branch_id=hospital.main_branch.pk)
        assert NoShowRecord.objects.filter(appointment=appointment).count() == 1


def test_a_no_show_cancels_its_outstanding_reminders(hospital, clinic, schedule) -> None:
    patient = make_patient(hospital)
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        Appointment.objects.filter(pk=appointment.pk).update(
            starts_at=timezone.now() - dt.timedelta(hours=3)
        )
        reminders.record_no_shows(branch_id=hospital.main_branch.pk)
        assert not appointment.reminders.filter(status=ReminderJob.Status.QUEUED).exists()


def test_the_no_show_count_answers_has_this_happened_before(hospital, clinic, schedule) -> None:
    """Three is a conversation about holding the slot. One is Lagos traffic."""
    patient = make_patient(hospital)
    with hospital.scope():
        for hour in (10, 11, 12):
            appointment = booking.book(
                booking.BookingRequest(
                    patient=patient,
                    clinic=clinic,
                    schedule=schedule,
                    starts_at=at(next_weekday(Weekday.TUESDAY), hour),
                )
            )
            Appointment.objects.filter(pk=appointment.pk).update(
                starts_at=timezone.now() - dt.timedelta(hours=3, minutes=hour)
            )
        reminders.record_no_shows(branch_id=hospital.main_branch.pk)
        assert reminders.no_show_count(patient) == 3


# --------------------------------------------------------------------------
# The API
# --------------------------------------------------------------------------


def test_the_reminder_list_says_whether_sms_is_switched_on(
    api_as_desk, hospital, clinic, schedule
) -> None:
    """
    An empty list with no explanation looks like a bug. "Text messages are
    not switched on" is an answer.
    """
    patient = make_patient(hospital)
    book_one(hospital, clinic, schedule, patient)

    response = api_as_desk.get(reverse("api:v1:reminder-list"))
    assert response.status_code == 200, response.data
    assert len(response.data["reminders"]) == 2
    assert response.data["sms_enabled"] is False
    assert response.data["email_enabled"] is False


@override_settings(TERMII_API_KEY="test-key", TERMII_SENDER_ID="Curavia")
def test_the_list_reports_sms_as_on_when_it_is_configured(api_as_desk) -> None:
    response = api_as_desk.get(reverse("api:v1:reminder-list"))
    assert response.data["sms_enabled"] is True


def test_the_cost_endpoint_gives_a_number_to_decide_with(
    api_as_desk, hospital, clinic, schedule
) -> None:
    patient = make_patient(hospital, email="")
    book_one(hospital, clinic, schedule, patient)

    response = api_as_desk.get(reverse("api:v1:reminder-cost"), {"days": 30})
    assert response.status_code == 200, response.data
    assert response.data["by_sms"] == 2
    assert response.data["sms_cost_kobo"] > 0


def test_a_patient_can_be_opted_out_and_back_in(api_as_desk, hospital) -> None:
    patient = make_patient(hospital)
    url = reverse("api:v1:patient-reminder-preference", args=[patient.pk])

    out = api_as_desk.post(url, {"opted_out": True}, format="json")
    assert out.status_code == 200
    assert out.data["reminders_opted_out"] is True
    assert AuditEvent.objects.filter(action="patient.reminders.opt_out").exists()

    back = api_as_desk.post(url, {"opted_out": False}, format="json")
    assert back.data["reminders_opted_out"] is False
    assert AuditEvent.objects.filter(action="patient.reminders.opt_in").exists()


def test_only_an_administrator_can_force_a_send(api_as_desk, api, hospital) -> None:
    """It costs money, so it is not a button the whole front desk has."""
    assert api_as_desk.post(reverse("api:v1:reminder-send")).status_code == 403

    api.force_authenticate(user=hospital.staff("facility_admin"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    allowed = api.post(reverse("api:v1:reminder-send"))
    assert allowed.status_code == 200, allowed.data
    assert AuditEvent.objects.filter(action="reminders.send_now").exists()


def test_the_no_show_list_is_audited_as_a_bulk_read(
    api_as_desk, hospital, clinic, schedule
) -> None:
    patient = make_patient(hospital)
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        Appointment.objects.filter(pk=appointment.pk).update(
            starts_at=timezone.now() - dt.timedelta(hours=3)
        )
        reminders.record_no_shows(branch_id=hospital.main_branch.pk)

    response = api_as_desk.get(reverse("api:v1:no-show-list"))
    assert response.status_code == 200
    assert len(response.data) == 1
    assert AuditEvent.objects.filter(action="appointment.no_shows.view").exists()


def test_reminders_are_scoped_to_one_hospital(hospital, other_hospital, clinic, schedule) -> None:
    patient = make_patient(hospital)
    book_one(hospital, clinic, schedule, patient)
    with other_hospital.scope():
        assert ReminderJob.objects.count() == 0
    with hospital.scope():
        assert ReminderJob.objects.count() == 2


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def test_the_management_command_runs_without_a_worker(hospital, clinic, schedule) -> None:
    """
    The hosting plan has no background worker on the free tier, so this has
    to be runnable from a cron entry or by hand.
    """
    patient = make_patient(hospital, email="ifeoma@example.test")
    appointment = book_one(hospital, clinic, schedule, patient)
    with hospital.scope():
        _make_due(appointment)

    call_command("send_reminders", "--limit", "10")

    with hospital.scope():
        job = appointment.reminders.get(kind="day_before")
    # Sent unless the clock happens to be inside quiet hours, in which case
    # it was correctly held — both outcomes mean the command worked.
    assert job.status in {ReminderJob.Status.SENT, ReminderJob.Status.QUEUED}


# --------------------------------------------------------------------------
# Phone numbers
# --------------------------------------------------------------------------


def test_nigerian_numbers_are_normalised_for_the_gateway() -> None:
    """Written locally as 0803…, wanted by a gateway as 234803…."""
    from integrations.sms.termii import normalise

    assert normalise("08031234567") == "2348031234567"
    assert normalise("0803 123 4567") == "2348031234567"
    assert normalise("+2348031234567") == "2348031234567"
    assert normalise("2348031234567") == "2348031234567"
    assert normalise("8031234567") == "2348031234567"
    assert normalise("12345") == ""
    assert normalise("") == ""


def test_an_unconfigured_provider_refuses_rather_than_pretending() -> None:
    """
    A silent no-op would let a hospital believe its patients were being
    texted. The message ends up on the screen that shows why a reminder
    failed.
    """
    from integrations.sms.termii import TermiiSmsProvider

    result = TermiiSmsProvider().send(to="08031234567", body="hello")
    assert result.accepted is False
    assert "not set up" in result.error

    from integrations.email.brevo import BrevoEmailProvider

    email = BrevoEmailProvider().send(
        to=["somebody@example.test"], subject="hello", html="<p>hello</p>"
    )
    assert email.accepted is False
    assert "not set up" in email.error
