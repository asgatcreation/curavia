"""
Starting a visit, moving it along, and telling every open board about it.

**One function writes a stage change, and it is the one that broadcasts.**
That is the rule the whole live board rests on. If a module could set
``encounter.stage`` directly, the boards would drift from the database and
the first anybody would know is a patient standing at a desk nobody has
called. So ``move_to`` is the only way a stage changes, it writes the event
and the column in one transaction, and it sends the update after that
transaction commits.

**After commit, not during.** ``transaction.on_commit`` rather than an inline
``group_send``: a board that has already drawn a patient as "with the doctor"
when the transaction then rolls back is showing something that never
happened, and it will keep showing it until somebody reloads.

**A failed broadcast never fails the write.** Redis being down is an
inconvenience — the boards stop updating and reconnect — whereas a nurse
unable to send a patient to the laboratory because a cache is down is a
hospital that has stopped working. So the send is wrapped, and the boards
re-fetch their whole state on reconnect precisely so a missed message heals
itself.
"""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

from apps.core.models import Department
from apps.journey.models import (
    CLOSING_STAGES,
    WAITING_STAGES,
    DisplayScreen,
    Encounter,
    JourneyEvent,
    Stage,
)
from apps.patients.models import Patient

logger = logging.getLogger("curavia.journey")


class JourneyError(Exception):
    """The move was refused. The message is shown to whoever pressed it."""


# ---------------------------------------------------------------------------
# Group names
#
# Always namespaced by organisation, as apps/core/ws.py requires: a group name
# another hospital could collide with is a live feed of their patients.
# ---------------------------------------------------------------------------


def board_group(organisation_id, branch_id) -> str:
    return f"org.{organisation_id}.branch.{branch_id}.journey"


def display_group(screen: DisplayScreen) -> str:
    """
    One group per screen, not per branch.

    Each television shows a different set of departments, so the filtering
    has to happen before the message is sent rather than in a client that
    has no business receiving the rest.
    """
    return f"org.{screen.organisation_id}.display.{screen.pk}"


# ---------------------------------------------------------------------------
# Starting and moving
# ---------------------------------------------------------------------------


@transaction.atomic
def start_encounter(
    *,
    patient: Patient,
    branch,
    department: Department | None = None,
    kind: str = Encounter.Kind.OUTPATIENT,
    appointment=None,
    ticket=None,
    reason: str = "",
    actor=None,
) -> Encounter:
    """
    Open a visit, or hand back the one already open.

    Not an error to call twice. Two clerks checking the same patient in is a
    normal Tuesday, and the database constraint would turn it into a 500 on
    a screen where somebody is standing waiting. One person in the building
    is one encounter.
    """
    if patient.merged_into_id is not None:
        raise JourneyError(
            "That record has been merged into another one. Use the surviving record."
        )

    existing = Encounter.objects.filter(
        patient=patient, branch=branch, status=Encounter.Status.OPEN
    ).first()
    if existing is not None:
        return existing

    encounter = Encounter(
        organisation_id=patient.organisation_id,
        branch=branch,
        patient=patient,
        kind=kind,
        department=department,
        appointment=appointment,
        ticket=ticket,
        reason=reason.strip()[:200],
        stage=Stage.ARRIVED,
        stage_since=timezone.now(),
        opened_by=actor,
    )
    encounter.full_clean(exclude=["organisation", "branch"])
    encounter.save()

    JourneyEvent.objects.create(
        organisation_id=encounter.organisation_id,
        branch=branch,
        encounter=encounter,
        from_stage="",
        to_stage=Stage.ARRIVED,
        department=department,
        actor=actor,
        minutes_in_previous_stage=0,
        previous_was_waiting=False,
    )
    _announce(encounter, reason="arrived")
    return encounter


@transaction.atomic
def move_to(
    encounter: Encounter,
    stage: str,
    *,
    department: Department | None = None,
    actor=None,
    note: str = "",
    attending=None,
) -> Encounter:
    """
    Send a patient to the next stage.

    The only way ``Encounter.stage`` ever changes. Locks the row, so two
    departments pressing at once produce two events in a defined order
    rather than one overwriting the other's column.
    """
    if stage not in Stage.values:
        raise JourneyError(f"There is no stage called {stage!r}.")

    encounter = Encounter.objects.select_for_update().get(pk=encounter.pk)
    if encounter.status != Encounter.Status.OPEN:
        raise JourneyError("That visit is closed.")
    if encounter.stage == stage and department is None:
        # Idempotent: somebody pressed twice, or two people pressed the same
        # button. Not an error, and not a second event on the timeline.
        return encounter

    now = timezone.now()
    previous = encounter.stage
    waited = encounter.minutes_in_stage(now)

    encounter.stage = stage
    encounter.stage_since = now
    fields = ["stage", "stage_since", "updated_at"]

    if department is not None:
        encounter.department = department
        fields.append("department")
    if attending is not None:
        encounter.attending = attending
        fields.append("attending")
    if stage in CLOSING_STAGES:
        encounter.status = Encounter.Status.CLOSED
        encounter.ended_at = now
        fields += ["status", "ended_at"]

    encounter.save(update_fields=sorted(set(fields)))

    JourneyEvent.objects.create(
        organisation_id=encounter.organisation_id,
        branch_id=encounter.branch_id,
        encounter=encounter,
        from_stage=previous,
        to_stage=stage,
        department=department or encounter.department,
        actor=actor,
        at=now,
        minutes_in_previous_stage=waited,
        previous_was_waiting=previous in WAITING_STAGES,
        note=note.strip()[:200],
    )

    _announce(encounter, reason="moved")
    return encounter


def close_encounter(encounter: Encounter, *, actor=None, note: str = "") -> Encounter:
    """Finish a visit. A thin wrapper so callers do not set the status by hand."""
    return move_to(encounter, Stage.DONE, actor=actor, note=note)


def abandon_encounter(encounter: Encounter, *, actor=None, note: str = "") -> Encounter:
    """
    They went home without being seen.

    Recorded as its own ending rather than as "finished", because the two
    mean opposite things about the hospital and a report that cannot tell
    them apart is worse than no report.
    """
    return move_to(encounter, Stage.LEFT, actor=actor, note=note or "Left without being seen.")


# ---------------------------------------------------------------------------
# The board's data
# ---------------------------------------------------------------------------


def board_rows(branch_id, *, now=None) -> list[dict[str, Any]]:
    """
    Everybody currently in the building, longest wait first.

    Ordered by wait rather than by arrival: the board exists to answer "who
    has been standing there longest", and a list that needs scanning to
    answer that is a list nobody uses.
    """
    now = now or timezone.now()
    rows = (
        Encounter.objects.filter(branch_id=branch_id, status=Encounter.Status.OPEN)
        .select_related("patient", "department", "ticket", "attending__user")
        .order_by("stage_since")
    )
    return [_row(encounter, now) for encounter in rows]


def _row(encounter: Encounter, now) -> dict[str, Any]:
    attending = getattr(encounter.attending, "user", None)
    return {
        "id": str(encounter.pk),
        "patient_id": str(encounter.patient_id),
        "patient_name": encounter.patient.full_name,
        "mrn": encounter.patient.mrn,
        "token": encounter.ticket.label if encounter.ticket_id and encounter.ticket else "",
        "stage": encounter.stage,
        "stage_label": encounter.get_stage_display(),
        # Checked on the object rather than the id: mypy narrows the first
        # and not the second, and both are the same question here.
        "department": encounter.department.name if encounter.department else "",
        "triage": encounter.triage_category,
        "kind": encounter.kind,
        "attending": (attending.get_full_name() or attending.email) if attending else "",
        "minutes_in_stage": encounter.minutes_in_stage(now),
        "minutes_in_hospital": encounter.minutes_in_hospital(now),
        "is_waiting": encounter.is_waiting,
        "started_at": encounter.started_at.isoformat(),
        "stage_since": encounter.stage_since.isoformat(),
    }


def display_rows(screen: DisplayScreen, *, now=None) -> dict[str, Any]:
    """
    What a television may show: numbers, never names.

    Enforced here rather than in the client. The screen's payload is built
    by this function and no other, so there is no code path in which a name
    reaches a waiting room — see ``DisplayScreen``.
    """
    from apps.scheduling.models import QueueTicket

    now = now or timezone.now()
    tickets = QueueTicket.objects.filter(
        branch_id=screen.branch_id,
        day=timezone.localdate(),
        status__in=QueueTicket.ACTIVE_STATUSES,
    ).select_related("department")

    chosen = list(screen.departments.values_list("pk", flat=True))
    if chosen:
        tickets = tickets.filter(department_id__in=chosen)

    called = [
        {
            "label": ticket.label,
            "department": ticket.department.name,
            "called_to": ticket.called_to,
            "called_at": ticket.called_at.isoformat() if ticket.called_at else None,
        }
        for ticket in tickets.filter(
            status__in=(QueueTicket.Status.CALLED, QueueTicket.Status.SERVING)
        ).order_by("-called_at")[:6]
    ]

    waiting_by_department: dict[str, int] = {}
    for ticket in tickets.filter(status=QueueTicket.Status.WAITING):
        # `department` is non-null on a ticket; the guard is for mypy, which
        # reads the FK as optional because the reverse accessor can be.
        name = ticket.department.name if ticket.department_id else ""
        waiting_by_department[name] = waiting_by_department.get(name, 0) + 1

    return {
        "screen": screen.name,
        "footer": screen.footer,
        "now_serving": called,
        "waiting": [
            {"department": name, "count": count}
            for name, count in sorted(waiting_by_department.items())
        ],
        "at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Broadcasting
# ---------------------------------------------------------------------------


def _announce(encounter: Encounter, *, reason: str) -> None:
    """
    Tell the boards, once this transaction has actually committed.

    See the module docstring for why this is ``on_commit`` and why a failure
    here is logged rather than raised.
    """
    organisation_id = encounter.organisation_id
    branch_id = encounter.branch_id
    encounter_id = encounter.pk

    def send() -> None:
        row = None
        try:
            fresh = (
                Encounter.objects.select_related(
                    "patient", "department", "ticket", "attending__user"
                )
                .filter(pk=encounter_id)
                .first()
            )
            if fresh is not None:
                row = _row(fresh, timezone.now())
        except Exception:  # the board is not worth a 500
            logger.exception("could not build a board row for encounter %s", encounter_id)
            return

        broadcast(
            board_group(organisation_id, branch_id),
            {"type": "broadcast", "event": "journey", "reason": reason, "row": row},
        )
        refresh_displays(organisation_id, branch_id)

    transaction.on_commit(send)


def broadcast(group: str, payload: dict[str, Any]) -> None:
    """
    Send one message to one group, and never let it break the caller.

    Redis being down should stop the boards updating, not stop the hospital
    working. Boards re-fetch everything on reconnect, so a dropped message
    heals itself.
    """
    layer = get_channel_layer()
    if layer is None:  # pragma: no cover - only without a channel layer
        return
    try:
        async_to_sync(layer.group_send)(group, payload)
    except Exception:  # see the docstring: the boards are not worth a 500
        logger.warning("could not broadcast to %s", group, exc_info=True)


def refresh_displays(organisation_id, branch_id) -> None:
    """
    Nudge every television at this branch to re-read its own numbers.

    The screens are sent a signal rather than a payload: each one shows a
    different set of departments, and building every screen's view on every
    single stage change would do a dozen queries for a message most of them
    would ignore. The nudge is cheap and the screen asks for what it needs.
    """
    try:
        screens = list(
            DisplayScreen.objects.filter(branch_id=branch_id, is_active=True).only(
                "pk", "organisation_id"
            )
        )
    except Exception:  # the board matters more than the television
        logger.warning("could not list display screens for branch %s", branch_id, exc_info=True)
        return

    for screen in screens:
        broadcast(display_group(screen), {"type": "broadcast", "event": "display_changed"})


def announce_queue_change(organisation_id, branch_id) -> None:
    """
    Called when a ticket is issued or called but no stage changed.

    Checking somebody in, calling the next number and marking a no-show all
    change what a television shows without touching an encounter, and a
    waiting room whose screen only updates when a doctor presses something
    is a waiting room where the number on the wall is wrong.
    """

    def send() -> None:
        refresh_displays(organisation_id, branch_id)

    transaction.on_commit(send)


__all__ = [
    "JourneyError",
    "abandon_encounter",
    "announce_queue_change",
    "board_group",
    "board_rows",
    "broadcast",
    "close_encounter",
    "display_group",
    "display_rows",
    "move_to",
    "refresh_displays",
    "start_encounter",
]
