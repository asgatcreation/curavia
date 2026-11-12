"""
Building the list, working through it, and handing it on.

Every rule lives here rather than in a view, because the ward screen, the
handover and (at milestone 10) the drug round all have to agree about what
is outstanding, and a rule enforced in one place is a rule missing from the
other two.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.nursing.models import (
    Handover,
    HandoverStatus,
    NursingTask,
    TaskKind,
    TaskStatus,
    hash_content,
)

logger = logging.getLogger("curavia.nursing")


class NursingError(Exception):
    """Refused for a reason worth showing the nurse who asked."""


#: How often observations are due, by NEWS2 band, from the RCP's own
#: response table in apps/clinical/news2.py. The shorter end of each range is
#: used: a ward that is late on the short end is late either way, and a list
#: that said "due in six hours" for a patient the guidance wants seen at four
#: would be quietly wrong in the only direction that matters.
OBSERVATION_INTERVALS: dict[str, dt.timedelta] = {
    "routine": dt.timedelta(hours=12),
    "low": dt.timedelta(hours=4),
    "medium": dt.timedelta(hours=1),
    # "Continuous monitoring" is not an interval, and pretending it is one
    # would put a task on a list for a patient who needs somebody standing
    # at the bed. Half an hour is how often the ward is asked to write
    # something down, and the escalation task below is what actually
    # carries the message.
    "high": dt.timedelta(minutes=30),
}

#: Which kinds of order become a nurse's task, and what sort of task.
ORDER_TASK_KINDS: dict[str, str] = {
    "nursing": TaskKind.CARE,
    "medication": TaskKind.MEDICATION,
    "laboratory": TaskKind.SAMPLE,
    "diet": TaskKind.CARE,
}


# ---------------------------------------------------------------------------
# Making tasks
# ---------------------------------------------------------------------------


@transaction.atomic
def add_task(
    *,
    patient,
    name: str,
    actor,
    branch=None,
    encounter=None,
    department=None,
    kind: str = TaskKind.CARE,
    detail: str = "",
    due_at=None,
    urgent: bool = False,
    order=None,
) -> NursingTask:
    """Put one thing on the list."""
    if kind not in TaskKind.values:
        raise NursingError(f"There is no kind of task called {kind!r}.")
    name = name.strip()
    if not name:
        raise NursingError("Say what has to be done.")

    task = NursingTask(
        organisation=patient.organisation,
        branch=branch or getattr(encounter, "branch", None) or patient.home_branch,
        patient=patient,
        encounter=encounter,
        department=department or getattr(encounter, "department", None),
        kind=kind,
        name=name[:200],
        detail=detail.strip(),
        due_at=due_at,
        urgent=urgent,
        order=order,
        created_by=actor,
        updated_by=actor,
    )
    task.full_clean(exclude=["organisation", "branch", "patient", "encounter", "department"])
    task.save()
    return task


@transaction.atomic
def tasks_from_orders(encounter) -> list[NursingTask]:
    """
    Turn this visit's signed orders into tasks a nurse can work through.

    Only signed orders: a draft is the doctor still thinking, and a ward
    that started acting on drafts would be acting on things nobody had
    agreed to. Idempotent, because the screen rebuilds this whenever it
    loads and a second task for one injection is how a patient gets two.
    """
    from apps.orders.models import Order, OrderStatus

    made: list[NursingTask] = []
    rows = Order.objects.filter(
        encounter=encounter,
        status__in=(OrderStatus.ORDERED, OrderStatus.IN_PROGRESS),
        kind__in=tuple(ORDER_TASK_KINDS),
    ).select_related("patient")

    existing = set(NursingTask.objects.filter(order__in=rows).values_list("order_id", flat=True))

    for order in rows:
        if order.pk in existing:
            continue
        made.append(
            add_task(
                patient=order.patient,
                encounter=encounter,
                branch=order.branch,
                department=getattr(encounter, "department", None),
                kind=ORDER_TASK_KINDS.get(order.kind, TaskKind.CARE),
                # A medicine's dose, route and frequency are the instruction,
                # so they belong on the one line a nurse reads. For everything
                # else `summary` already folds the instructions in, and using
                # it here would print them twice — once on the line and again
                # underneath it.
                name=(order.summary if order.kind == "medication" else order.name),
                detail=order.instructions,
                # A stat order is due now; everything else is due when the
                # ward gets to it. Inventing a deadline for a routine order
                # would turn the whole list red by the afternoon and teach
                # the ward to ignore the colour.
                due_at=timezone.now() if order.priority == "stat" else None,
                urgent=order.priority in ("urgent", "stat"),
                order=order,
                actor=order.ordered_by,
            )
        )
    if made:
        logger.info("%s task(s) raised from orders on encounter %s", len(made), encounter.pk)
    return made


@transaction.atomic
def observation_tasks(encounter) -> NursingTask | None:
    """
    Put "observations due" on the list when they are.

    Derived from the NEWS2 band stored on the last set of observations, so
    the interval is the one the nurse was shown at the time rather than one
    recomputed from a since-changed algorithm.

    Returns the task if one was raised, None if observations are not due or
    one is already open. Never raises a second: a patient with two
    "observations due" lines is a patient whose list nobody trusts.
    """
    from apps.clinical.models import Observation

    latest = Observation.objects.filter(encounter=encounter).order_by("-taken_at").first()
    if latest is None or not latest.news2_band:
        return None

    interval = OBSERVATION_INTERVALS.get(latest.news2_band)
    if interval is None:
        return None

    due_at = latest.taken_at + interval
    if due_at > timezone.now():
        return None

    already = NursingTask.objects.filter(
        encounter=encounter, kind=TaskKind.OBSERVATION, status=TaskStatus.PENDING
    ).exists()
    if already:
        return None

    return add_task(
        patient=encounter.patient,
        encounter=encounter,
        branch=encounter.branch,
        department=getattr(encounter, "department", None),
        kind=TaskKind.OBSERVATION,
        name="Observations due",
        detail=(
            f"Last taken {timezone.localtime(latest.taken_at):%H:%M}. "
            f"Scoring {latest.news2_band} on NEWS2."
        ),
        due_at=due_at,
        urgent=latest.news2_band in ("medium", "high"),
        actor=None,
    )


def refresh(encounter) -> list[NursingTask]:
    """Everything the system can work out for itself, in one call."""
    made = tasks_from_orders(encounter)
    observation = observation_tasks(encounter)
    if observation is not None:
        made.append(observation)
    return made


# ---------------------------------------------------------------------------
# Working through it
# ---------------------------------------------------------------------------


@transaction.atomic
def complete(*, task: NursingTask, actor, note: str = "") -> NursingTask:
    """Done, with a time and a name against it."""
    if task.status != TaskStatus.PENDING:
        raise NursingError("That task is already closed.")

    task.status = TaskStatus.DONE
    task.done_at = timezone.now()
    task.done_by = actor
    task.done_by_name = ((actor.get_full_name() or actor.email) if actor else "")[:200]
    task.done_note = note.strip()
    task.updated_by = actor
    task.save(
        update_fields=[
            "status",
            "done_at",
            "done_by",
            "done_by_name",
            "done_note",
            "updated_by",
            "updated_at",
        ]
    )
    return task


@transaction.atomic
def mark_not_done(*, task: NursingTask, reason: str, actor=None) -> NursingTask:
    """
    Closed without being done, and the reason is required.

    "Patient off the ward for a scan" and "patient refused" are different
    clinical facts that lead to different next steps, and a blank reason is
    how a ward loses the difference between them.
    """
    if task.status != TaskStatus.PENDING:
        raise NursingError("That task is already closed.")
    if not reason.strip():
        raise NursingError("Say why this was not done. It is not the same as done.")

    task.status = TaskStatus.NOT_DONE
    task.not_done_reason = reason.strip()
    task.done_at = timezone.now()
    task.done_by = actor
    task.done_by_name = ((actor.get_full_name() or actor.email) if actor else "")[:200]
    task.updated_by = actor
    task.save(
        update_fields=[
            "status",
            "not_done_reason",
            "done_at",
            "done_by",
            "done_by_name",
            "updated_by",
            "updated_at",
        ]
    )
    return task


@transaction.atomic
def cancel(*, task: NursingTask, reason: str, actor=None) -> NursingTask:
    """
    It should not have been asked for at all.

    Separate from "not done", because they answer different questions. Not
    done means the patient still needs it; cancelled means they never did.
    """
    if task.status != TaskStatus.PENDING:
        raise NursingError("That task is already closed.")
    if not reason.strip():
        raise NursingError("Say why this task is being cancelled.")

    task.status = TaskStatus.CANCELLED
    task.not_done_reason = reason.strip()
    task.updated_by = actor
    task.save(update_fields=["status", "not_done_reason", "updated_by", "updated_at"])
    return task


def open_tasks(*, department=None, branch=None, patient=None) -> list[NursingTask]:
    """What is still to do. The ward list, in the order it should be worked."""
    rows = NursingTask.objects.filter(status=TaskStatus.PENDING).select_related("patient", "order")
    if department is not None:
        rows = rows.filter(department=department)
    if branch is not None:
        rows = rows.filter(branch=branch)
    if patient is not None:
        rows = rows.filter(patient=patient)
    return list(rows)


def tasks_for_patient(patient, *, limit: int = 100) -> list[NursingTask]:
    return list(NursingTask.objects.filter(patient=patient).select_related("done_by")[:limit])


# ---------------------------------------------------------------------------
# Handover
# ---------------------------------------------------------------------------


def _snapshot(task: NursingTask) -> dict[str, Any]:
    """One outstanding task, as the handover will keep it for good."""
    return {
        "task_id": str(task.pk),
        "patient": task.patient.full_name,
        "patient_id": str(task.patient_id),
        "kind": task.get_kind_display(),
        "name": task.name,
        "detail": task.detail,
        "due_at": task.due_at.isoformat() if task.due_at else "",
        "overdue": task.is_overdue,
        "minutes_late": task.minutes_late,
        "urgent": task.urgent,
        "handovers": task.handovers,
    }


@transaction.atomic
def open_handover(*, department, actor, branch=None, shift: str = "") -> Handover:
    """
    Start writing one, or pick up the one already being written.

    One draft per nurse per ward: two half-written sheets is how a ward
    hands over twice and means neither.
    """
    existing = Handover.objects.filter(
        department=department, from_nurse=actor, status=HandoverStatus.DRAFT
    ).first()
    if existing is not None:
        return existing

    name = ((actor.get_full_name() or actor.email) if actor else "").strip()
    handover = Handover(
        organisation=department.organisation,
        branch=branch or department.branch,
        department=department,
        shift=shift.strip()[:60],
        from_nurse=actor,
        from_nurse_name=name[:200],
        created_by=actor,
        updated_by=actor,
    )
    handover.save()
    return handover


@transaction.atomic
def save_draft(*, handover: Handover, content: dict, actor=None) -> Handover:
    if handover.is_signed:
        raise NursingError("That handover has been given to the next shift.")

    handover.content = content or {}
    handover.updated_by = actor
    handover.save(update_fields=["content", "updated_by", "updated_at"])
    return handover


@transaction.atomic
def sign(*, handover: Handover, actor, membership=None, shift: str = "") -> Handover:
    """
    Hand it over.

    The outstanding list is built here rather than taken from the client,
    which is the whole point of the feature: a handover sheet typed from
    memory omits the thing nobody remembered. Every open task on the ward
    goes into the snapshot, and each one's handover count goes up, so a task
    on its third shift is visible as such.
    """
    if handover.is_signed:
        raise NursingError("That handover has already been given to the next shift.")
    if handover.from_nurse_id != getattr(actor, "pk", None):
        raise NursingError("A handover is signed by the nurse who wrote it.")

    locked = Handover.objects.select_for_update().get(pk=handover.pk)
    if locked.is_signed:  # two taps, two requests, one handover
        raise NursingError("That handover has already been given to the next shift.")

    still_open = open_tasks(department=locked.department)
    locked.outstanding = [_snapshot(task) for task in still_open]
    locked.status = HandoverStatus.SIGNED
    locked.signed_at = timezone.now()
    locked.content_hash = hash_content(locked.content)
    if shift.strip():
        locked.shift = shift.strip()[:60]
    locked.from_nurse_role = (getattr(membership, "job_title", "") or "")[:120]
    locked.updated_by = actor
    locked.save()

    # Each one's count goes up, so a task on its third shift is visible as
    # such without anybody having to notice.
    NursingTask.objects.filter(pk__in=[task.pk for task in still_open]).update(
        handovers=F("handovers") + 1
    )
    logger.info(
        "handover %s signed with %d task(s) outstanding", locked.pk, len(locked.outstanding)
    )
    return locked


@transaction.atomic
def accept(*, handover: Handover, actor, note: str = "") -> Handover:
    """
    The nurse coming on takes it.

    A separate act, because a handover nobody received is a handover that
    did not happen, and the ward has to be able to tell that apart from one
    that was never written. The nurse who gave it cannot accept it on the
    other's behalf.
    """
    if handover.status == HandoverStatus.DRAFT:
        raise NursingError("That handover has not been given yet.")
    if handover.status == HandoverStatus.ACCEPTED:
        raise NursingError("That handover has already been received.")
    if handover.from_nurse_id == getattr(actor, "pk", None):
        raise NursingError("A handover is received by the nurse coming on, not the one going off.")

    handover.status = HandoverStatus.ACCEPTED
    handover.accepted_at = timezone.now()
    handover.to_nurse = actor
    handover.to_nurse_name = ((actor.get_full_name() or actor.email) if actor else "")[:200]
    handover.accepted_note = note.strip()
    handover.updated_by = actor
    handover.save(
        update_fields=[
            "status",
            "accepted_at",
            "to_nurse",
            "to_nurse_name",
            "accepted_note",
            "updated_by",
            "updated_at",
        ]
    )
    return handover


def handovers_for(department, *, limit: int = 20) -> list[Handover]:
    return list(
        Handover.objects.filter(department=department).select_related("from_nurse", "to_nurse")[
            :limit
        ]
    )


def latest_received(department) -> Handover | None:
    """
    The last one handed over, for the nurse coming on to read.

    Includes one already accepted: the nurse on at two o'clock still needs
    to be able to re-read what she was told at the start of her shift.
    """
    return (
        Handover.objects.filter(
            department=department,
            status__in=(HandoverStatus.SIGNED, HandoverStatus.ACCEPTED),
        )
        .order_by("-signed_at")
        .first()
    )


__all__ = [
    "OBSERVATION_INTERVALS",
    "ORDER_TASK_KINDS",
    "NursingError",
    "accept",
    "add_task",
    "cancel",
    "complete",
    "handovers_for",
    "latest_received",
    "mark_not_done",
    "observation_tasks",
    "open_handover",
    "open_tasks",
    "refresh",
    "save_draft",
    "sign",
    "tasks_for_patient",
    "tasks_from_orders",
]
