"""
Appointment reminders, and what happens when somebody does not come.

Reminders are the cheapest thing a hospital can do about no-shows, and the
easiest to get wrong in ways that are worse than not doing it at all. Five
decisions, in the order of how much harm they prevent.

**1. A reminder never says what the appointment is for.**

Not the clinic, not the department, not the doctor's speciality. "You have an
appointment at Lifeline Specialist Hospital on Tuesday 4 November at 10:00"
and nothing more.

A text message is read on a lock screen, on a handset that is shared with a
husband, a mother, a landlord. "Your appointment at the HIV clinic" or "with
the psychiatrist" or "at the fertility centre" is a disclosure to whoever
happens to be looking, made by us, about a person who never agreed to it. The
hospital's own name is the most that can be said, and even that is a
deliberate call: a patient needs to recognise the message as real.

**2. Exactly once, or not at all.**

A job row per (appointment, kind) with a unique constraint, claimed with a
lock before sending. Retries are the normal case for queued work, and a retry
that texts somebody twice costs the hospital money and makes the system look
broken to the patient.

**3. Nobody is woken at three in the morning.**

Quiet hours, applied in the hospital's own timezone. A reminder that arrives
at 03:14 is not a reminder, it is a complaint.

**4. Opting out is honoured everywhere, immediately.**

One flag on the patient, checked at send time rather than at schedule time, so
somebody who says "stop" after a job was queued still does not get it.

**5. Text messages cost money and email does not.**

So email is tried first wherever there is an address, SMS is used where there
is not, and the hospital can switch SMS off entirely and still have working
reminders. ``estimate_cost`` exists so the decision is made with a number
rather than a feeling.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.scheduling.models import Appointment
from integrations.base import get_provider

logger = logging.getLogger("curavia.reminders")

#: How long before the appointment each reminder goes out.
#:
#: Two, not one. The day-before message is the one that changes behaviour —
#: it is when somebody arranges transport or takes the morning off — and the
#: two-hour one catches the person who forgot on the day.
LEAD_TIMES: dict[str, dt.timedelta] = {
    "day_before": dt.timedelta(days=1),
    "same_day": dt.timedelta(hours=2),
}

#: Nothing is sent outside these hours, in the hospital's local time.
QUIET_START = dt.time(20, 0)
QUIET_END = dt.time(7, 30)

#: A message that has failed this many times is not tried again. Each attempt
#: may cost money, and a number that is wrong stays wrong.
MAX_ATTEMPTS = 3

#: A job this far past its send time is skipped rather than sent. Reminding
#: somebody about an appointment that has already started is worse than
#: silence.
TOO_LATE = dt.timedelta(hours=1)


class ReminderError(Exception):
    """Something was wrong with the request, not with the provider."""


def within_quiet_hours(when: dt.datetime) -> bool:
    """True when a message would land in the middle of the night."""
    local = timezone.localtime(when).time()
    # The window crosses midnight, so it is "after 20:00 OR before 07:30"
    # rather than a simple between.
    return local >= QUIET_START or local < QUIET_END


def next_sendable_time(when: dt.datetime) -> dt.datetime:
    """
    Move a send time out of quiet hours, forwards to the morning.

    Forwards, never backwards: a reminder arriving earlier than intended is a
    reminder about the wrong day.
    """
    if not within_quiet_hours(when):
        return when
    local = timezone.localtime(when)
    morning = local.replace(hour=QUIET_END.hour, minute=QUIET_END.minute, second=0, microsecond=0)
    if local.time() >= QUIET_START:
        morning += dt.timedelta(days=1)
    return morning


# ---------------------------------------------------------------------------
# What a reminder says
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Message:
    subject: str
    text: str
    html: str


def compose(appointment: Appointment) -> Message:
    """
    The words.

    Deliberately short, deliberately vague about the reason, and it names the
    hospital so the patient knows it is real. See decision 1 in the module
    docstring for why the clinic is not mentioned.
    """
    local = timezone.localtime(appointment.starts_at)
    hospital = appointment.organisation.name
    when = (
        f"{local:%A %-d %B} at {local:%H:%M}"
        if _supports_dash()
        else (f"{local.strftime('%A')} {local.day} {local.strftime('%B')} at {local:%H:%M}")
    )
    first_name = appointment.patient.first_name or "there"

    text = (
        f"Hello {first_name}. This is a reminder of your appointment at "
        f"{hospital} on {when}. Please bring your hospital card. "
        f"If you cannot come, please call us."
    )
    html = (
        f"<p>Hello {first_name},</p>"
        f"<p>This is a reminder of your appointment at <strong>{hospital}</strong> "
        f"on <strong>{when}</strong>.</p>"
        f"<p>Please bring your hospital card. If you cannot come, please call us "
        f"so somebody else can have the time.</p>"
    )
    return Message(subject=f"Your appointment at {hospital}", text=text, html=html)


def _supports_dash() -> bool:
    """``%-d`` is a glibc extension and is not available on Windows."""
    try:
        dt.datetime(2026, 1, 5).strftime("%-d")
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


def schedule_for(appointment: Appointment) -> list[Any]:
    """
    Queue the reminders for one appointment.

    Idempotent: calling it again for the same appointment creates nothing new,
    which matters because it is called on booking *and* on reschedule, and a
    patient who moved their appointment twice should not get six messages.
    """
    # Imported here, not at module level: models.py imports this module
    # for its own helpers, and at the top the two would import each other.
    from apps.scheduling.models import ReminderJob

    if not appointment.is_live:
        return []

    now = timezone.now()
    jobs: list[ReminderJob] = []
    for _kind, lead in LEAD_TIMES.items():
        kind = _kind
        send_at = next_sendable_time(appointment.starts_at - lead)
        if send_at >= appointment.starts_at:
            # The reminder would land after the appointment began.
            continue
        if send_at <= now:
            # Already in the past, because the appointment was booked inside
            # the lead time — this morning, for this afternoon. Queueing it
            # would fire a "see you tomorrow" message the instant the clerk
            # pressed Book, which costs money and reads as a mistake.
            continue
        try:
            with transaction.atomic():
                jobs.append(
                    ReminderJob.objects.create(
                        organisation_id=appointment.organisation_id,
                        branch_id=appointment.branch_id,
                        appointment=appointment,
                        patient=appointment.patient,
                        kind=kind,
                        send_at=send_at,
                    )
                )
        except IntegrityError:
            # Already queued. The unique constraint is the authority on this,
            # not a prior read, because two clerks can reschedule at once.
            continue
    return jobs


def cancel_for(appointment: Appointment, *, reason: str = "") -> int:
    """
    Drop the queued reminders for an appointment.

    Called when it is cancelled or moved. A reminder for an appointment that
    no longer exists sends somebody across Lagos for nothing, which is the
    single most damaging thing this feature could do.
    """
    from apps.scheduling.models import ReminderJob

    return ReminderJob.objects.filter(
        appointment=appointment, status=ReminderJob.Status.QUEUED
    ).update(
        status=ReminderJob.Status.CANCELLED,
        failure_reason=reason[:200] or "The appointment changed.",
        updated_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SendOutcome:
    sent: int
    skipped: int
    failed: int
    cost_kobo: int


def due_jobs(*, now: dt.datetime | None = None, limit: int = 200):
    """Everything ready to go, oldest first."""
    from apps.scheduling.models import ReminderJob

    now = now or timezone.now()
    return (
        ReminderJob.objects.filter(status=ReminderJob.Status.QUEUED, send_at__lte=now)
        .select_related("appointment", "patient", "organisation")
        .order_by("send_at")[:limit]
    )


def send_due(*, now: dt.datetime | None = None, limit: int = 200) -> SendOutcome:
    """
    Send every reminder that is due.

    Run from a scheduled job, and safe to run twice: each row is claimed
    under a lock before anything is sent.
    """
    now = now or timezone.now()
    sent = skipped = failed = cost = 0

    for job in list(due_jobs(now=now, limit=limit)):
        outcome = send_one(job, now=now)
        if outcome is None:
            skipped += 1
        elif outcome.accepted:
            sent += 1
            cost += outcome.cost_kobo
        else:
            failed += 1
    return SendOutcome(sent=sent, skipped=skipped, failed=failed, cost_kobo=cost)


@dataclass(frozen=True, slots=True)
class Delivery:
    accepted: bool
    channel: str
    cost_kobo: int = 0
    error: str = ""


def send_one(job, *, now: dt.datetime | None = None) -> Delivery | None:
    """
    Send one reminder, or decide not to. ``None`` means deliberately skipped.

    The row is locked and re-read inside the transaction, so two workers
    picking up the same job produce one message.
    """
    from apps.scheduling.models import ReminderJob

    now = now or timezone.now()

    with transaction.atomic():
        job = ReminderJob.objects.select_for_update().get(pk=job.pk)
        if job.status != ReminderJob.Status.QUEUED:
            return None

        appointment = job.appointment
        patient = job.patient

        # Checked now rather than when it was queued: somebody who said
        # "stop" yesterday must not get a message queued the day before.
        if patient.reminders_opted_out:
            job.finish(ReminderJob.Status.SKIPPED, "The patient has opted out of reminders.")
            return None
        if not appointment.is_live:
            job.finish(ReminderJob.Status.CANCELLED, "The appointment is no longer open.")
            return None
        if now > appointment.starts_at + TOO_LATE:
            job.finish(ReminderJob.Status.SKIPPED, "Too late to be useful.")
            return None
        if within_quiet_hours(now):
            # Pushed to the morning rather than dropped.
            job.send_at = next_sendable_time(now)
            job.save(update_fields=["send_at", "updated_at"])
            return None

        message = compose(appointment)
        job.attempts += 1

        delivery = _deliver(patient, message)
        if delivery is None:
            job.finish(ReminderJob.Status.SKIPPED, "No phone number or email address on file.")
            return None

        if delivery.accepted:
            job.channel = delivery.channel
            job.cost_kobo = delivery.cost_kobo
            job.finish(ReminderJob.Status.SENT, "")
            return delivery

        if job.attempts >= MAX_ATTEMPTS:
            job.finish(ReminderJob.Status.FAILED, delivery.error)
        else:
            # Backed off, so a provider having a bad minute is not hammered.
            job.send_at = now + dt.timedelta(minutes=15 * job.attempts)
            job.failure_reason = delivery.error[:200]
            job.save(update_fields=["attempts", "send_at", "failure_reason", "updated_at"])
        return delivery


def _deliver(patient, message: Message) -> Delivery | None:
    """
    Email where there is an address, otherwise a text message.

    Email first because it is free and SMS is not — see decision 5. A
    hospital with no SMS keys still has working reminders for every patient
    who gave an email address.
    """
    if patient.email:
        result = get_provider("email").send(
            to=[patient.email],
            subject=message.subject,
            html=message.html,
            text=message.text,
        )
        return Delivery(
            accepted=result.accepted,
            channel="email",
            error=result.error or "",
        )

    if patient.phone:
        result = get_provider("sms").send(to=patient.phone, body=message.text)
        return Delivery(
            accepted=result.accepted,
            channel="sms",
            cost_kobo=result.cost_kobo,
            error=result.error or "",
        )

    return None


# ---------------------------------------------------------------------------
# What it costs
# ---------------------------------------------------------------------------


def estimate_cost(*, organisation_id, days: int = 30) -> dict[str, int]:
    """
    What reminders would cost over the next month, in kobo.

    Shown before SMS is switched on, because "about ₦4,200 a month" is a
    decision a hospital owner can make and "enable SMS reminders?" is not.
    Counts patients with no email address, since those are the ones that
    would be texted.
    """
    from apps.scheduling.models import ReminderJob

    now = timezone.now()
    horizon = now + dt.timedelta(days=days)
    queued = ReminderJob.objects.filter(
        organisation_id=organisation_id,
        status=ReminderJob.Status.QUEUED,
        send_at__lte=horizon,
    ).select_related("patient")

    by_sms = sum(1 for job in queued if not job.patient.email and job.patient.phone)
    by_email = sum(1 for job in queued if job.patient.email)
    no_contact = sum(1 for job in queued if not job.patient.email and not job.patient.phone)

    from integrations.sms.termii import ESTIMATED_COST_KOBO

    return {
        "days": days,
        "by_email": by_email,
        "by_sms": by_sms,
        "no_contact": no_contact,
        "sms_cost_kobo": by_sms * ESTIMATED_COST_KOBO,
    }


# ---------------------------------------------------------------------------
# Not turning up
# ---------------------------------------------------------------------------


def record_no_shows(*, branch_id, grace_minutes: int = 60) -> int:
    """
    Close off appointments nobody came to, and keep a record of it.

    The record is per patient rather than only per appointment, because the
    question a clerk asks is "has this person done this before?" — three
    no-shows is a conversation about whether the slot should be held, and one
    is Lagos traffic.

    An hour's grace, for the same reason: somebody forty minutes late is
    somebody who came.
    """
    from apps.scheduling.models import NoShowRecord, ReminderJob

    cutoff = timezone.now() - dt.timedelta(minutes=grace_minutes)
    missed = list(
        Appointment.objects.filter(
            branch_id=branch_id,
            starts_at__lt=cutoff,
            status=Appointment.Status.BOOKED,
        ).select_related("patient", "clinic")
    )

    for appointment in missed:
        with transaction.atomic():
            Appointment.objects.filter(pk=appointment.pk).update(
                status=Appointment.Status.NO_SHOW, updated_at=timezone.now()
            )
            NoShowRecord.objects.get_or_create(
                organisation_id=appointment.organisation_id,
                branch_id=appointment.branch_id,
                appointment=appointment,
                defaults={
                    "patient": appointment.patient,
                    "missed_at": appointment.starts_at,
                    # Recorded, because "they never got a reminder" changes
                    # whose problem the no-show is.
                    "was_reminded": ReminderJob.objects.filter(
                        appointment=appointment, status=ReminderJob.Status.SENT
                    ).exists(),
                },
            )
        ReminderJob.objects.filter(
            appointment=appointment, status=ReminderJob.Status.QUEUED
        ).update(status=ReminderJob.Status.CANCELLED, updated_at=timezone.now())

    return len(missed)


def no_show_count(patient) -> int:
    from apps.scheduling.models import NoShowRecord

    return NoShowRecord.objects.filter(patient=patient).count()


__all__ = [
    "LEAD_TIMES",
    "MAX_ATTEMPTS",
    "QUIET_END",
    "QUIET_START",
    "Delivery",
    "Message",
    "ReminderError",
    "SendOutcome",
    "cancel_for",
    "compose",
    "due_jobs",
    "estimate_cost",
    "next_sendable_time",
    "no_show_count",
    "record_no_shows",
    "schedule_for",
    "send_due",
    "send_one",
    "within_quiet_hours",
]
