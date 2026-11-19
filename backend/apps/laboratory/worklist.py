"""
The laboratory's day: work arriving, blood being drawn, tubes booked in.

This module owns every change of state between a doctor signing an order and
the bench starting work. Four things happen here, and the order of them is
the whole design.

**Work arrives by itself.** ``open_requests_for`` is called when the order
basket is signed. Nobody re-types a request list into a laboratory system;
the moment the doctor signs, the test is on the laboratory's screen and the
clock on the hospital's promise to the ward has started.

**Tubes, not tests.** ``plan_collection`` is the one piece of real thinking.
A patient with a full blood count, a packed cell volume and an E/U/Cr needs
**two** tubes, not three: the first two are both EDTA. Working that out from
the specimen on each panel is what stops a nurse drawing three tubes from a
dehydrated child, and it is why ``collect`` takes a list of requests and
produces however many samples the specimens require.

**Draw, then label.** The accession number is allocated inside ``collect``,
after the blood is in the tube. See ``Sample``.

**A rejection is an event, not a deletion.** ``reject`` keeps the tube, its
reason and the person who refused it, and puts the tests back on the
collection list with their rejection count raised. The ward then sees that
this is a second attempt, which is the single most useful thing a redraw
list can tell the person holding the needle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils import timezone

from apps.core import audit
from apps.core.request_context import as_audit_kwargs
from apps.laboratory.models import (
    OPEN_REQUEST_STATUSES,
    LabPanel,
    LabRequest,
    RejectionReason,
    RequestStatus,
    Sample,
    SampleStatus,
    Specimen,
    next_accession_number,
)
from apps.orders.models import Order, OrderKind, OrderStatus
from apps.orders.service import OrderError, advance

logger = logging.getLogger("curavia.laboratory")


class WorklistError(Exception):
    """Refused. The message is shown to the member of staff as written."""


# ---------------------------------------------------------------------------
# Work arriving
# ---------------------------------------------------------------------------


def open_requests_for(orders: list[Order], *, actor=None) -> list[LabRequest]:
    """
    Turn freshly signed laboratory orders into laboratory requests.

    Called from ``orders.service.sign``. Silently skips an order that is not
    a laboratory one, and an order whose service has no panel yet — the
    second is a real situation, not an error: the setup wizard creates the
    billable service and the laboratory supervisor may not have described
    the test on the bench yet. Those orders still exist, still get charged
    and still show on the doctor's list; they simply do not appear on a
    worklist that could not say what tube to draw.
    """
    wanted = [order for order in orders if order.kind == OrderKind.LABORATORY and order.service_id]
    if not wanted:
        return []

    panels = {
        panel.service_id: panel
        for panel in LabPanel.objects.filter(
            service_id__in=[order.service_id for order in wanted], is_active=True
        ).select_related("service")
    }

    created: list[LabRequest] = []
    for order in wanted:
        panel = panels.get(order.service_id)
        if panel is None:
            logger.info("order %s has no laboratory panel; not put on the worklist", order.pk)
            continue
        if LabRequest.objects.filter(order_id=order.pk).exists():
            continue
        created.append(_create_request(order=order, panel=panel, actor=actor))

    if created:
        _broadcast(created[0].organisation_id, created[0].branch_id, reason="requested")
    return created


def _create_request(*, order: Order, panel: LabPanel, actor=None) -> LabRequest:
    requested_at = order.ordered_at or timezone.now()
    request = LabRequest(
        organisation_id=order.organisation_id,
        branch_id=order.branch_id,
        order=order,
        patient_id=order.patient_id,
        encounter_id=order.encounter_id,
        panel=panel,
        specimen=panel.specimen,
        container=panel.container,
        priority=order.priority,
        requested_at=requested_at,
        requested_by=actor,
        due_at=requested_at + timedelta(minutes=panel.turnaround_minutes),
        created_by=actor,
        updated_by=actor,
    )
    request.save()
    return request


# ---------------------------------------------------------------------------
# Planning the draw
# ---------------------------------------------------------------------------


@dataclass
class Tube:
    """One tube to draw, and the tests it will carry."""

    specimen: str
    specimen_label: str
    container: str
    requests: list[LabRequest] = field(default_factory=list)

    @property
    def panel_names(self) -> str:
        return ", ".join(request.panel.code for request in self.requests)

    def as_dict(self) -> dict[str, Any]:
        return {
            "specimen": self.specimen,
            "specimen_label": self.specimen_label,
            "container": self.container,
            "panels": [
                {"id": str(request.pk), "code": request.panel.code, "name": request.panel.name}
                for request in self.requests
            ],
        }


def plan_collection(requests: list[LabRequest]) -> list[Tube]:
    """
    How many tubes, of what, for these tests.

    Grouped by specimen type, because that is what decides whether one tube
    can serve two tests. The container description comes from the first
    request in each group: two EDTA panels could in principle disagree about
    the volume, and the honest answer is to draw the tube the first one asks
    for rather than to invent a compromise nobody wrote down.

    Ordered by specimen so the list a nurse reads is stable between
    refreshes. A list that reshuffles is a list that gets mis-read.
    """
    tubes: dict[str, Tube] = {}
    for request in requests:
        tube = tubes.get(request.specimen)
        if tube is None:
            tube = Tube(
                specimen=request.specimen,
                specimen_label=str(Specimen(request.specimen).label),
                container=request.container,
            )
            tubes[request.specimen] = tube
        elif not tube.container:
            tube.container = request.container
        tube.requests.append(request)

    return [tubes[key] for key in sorted(tubes)]


# ---------------------------------------------------------------------------
# Collecting
# ---------------------------------------------------------------------------


@transaction.atomic
def collect(
    *,
    requests: list[LabRequest],
    actor=None,
    note: str = "",
    replaces: Sample | None = None,
    now=None,
) -> list[Sample]:
    """
    Draw the tubes for these tests.

    Returns one ``Sample`` per specimen type, each with its accession number
    already allocated — the caller prints a label per returned sample.

    Refuses the whole list if any of it is not waiting for collection.
    Drawing blood for a test that already has a tube on the bench is how a
    patient gets stuck twice for one result, and a partial success here would
    leave the nurse unsure which of the three tubes in their hand was the
    one that counted.

    All the requests must be for one patient, for the same reason: this
    function allocates one tube per specimen, and a tube shared between two
    patients does not exist.
    """
    if not requests:
        raise WorklistError("Choose at least one test to collect.")

    patients = {request.patient_id for request in requests}
    if len(patients) > 1:
        raise WorklistError("One collection is for one patient.")

    not_waiting = [
        request.panel.code
        for request in requests
        if request.status != RequestStatus.AWAITING_COLLECTION
    ]
    if not_waiting:
        raise WorklistError(
            "A sample has already been taken for " + ", ".join(sorted(not_waiting)) + "."
        )

    now = now or timezone.now()
    samples: list[Sample] = []
    for tube in plan_collection(requests):
        sample = Sample(
            organisation_id=requests[0].organisation_id,
            branch_id=requests[0].branch_id,
            accession=next_accession_number(requests[0].organisation_id),
            patient_id=requests[0].patient_id,
            specimen=tube.specimen,
            container=tube.container,
            panel_names=tube.panel_names[:300],
            status=SampleStatus.COLLECTED,
            collected_at=now,
            collected_by=actor,
            collection_note=note.strip()[:300],
            recollection_of=replaces,
            created_by=actor,
            updated_by=actor,
        )
        sample.save()

        for request in tube.requests:
            request.sample = sample
            request.status = RequestStatus.COLLECTED
            request.updated_by = actor
            request.save(update_fields=["sample", "status", "updated_by", "updated_at"])

        samples.append(sample)
        audit.record(
            "lab.sample.collected",
            actor=actor,
            object_type="laboratory.Sample",
            object_id=sample.pk,
            patient_id=sample.patient_id,
            changes={
                "accession": sample.accession,
                "specimen": sample.specimen,
                "panels": sample.panel_names,
                "replaces": str(replaces.accession) if replaces else "",
            },
            **as_audit_kwargs(),
        )

    _broadcast(samples[0].organisation_id, samples[0].branch_id, reason="collected")
    return samples


# ---------------------------------------------------------------------------
# Booking in, and refusing
# ---------------------------------------------------------------------------


@transaction.atomic
def receive(*, sample: Sample, actor=None, now=None) -> Sample:
    """
    Book a tube in at the laboratory, and start work on what is in it.

    This is the moment the doctor's order becomes "being done": the tube is
    in the building and somebody is accountable for it. Receiving also moves
    every test on the tube to ``IN_PROGRESS``, because in a small laboratory
    booking in and putting it on the analyser are the same walk across the
    room, and a separate "start" button that nobody presses would make the
    worklist lie.
    """
    if sample.status == SampleStatus.RECEIVED:
        raise WorklistError(f"{sample.accession} has already been received.")
    if sample.status == SampleStatus.REJECTED:
        raise WorklistError(f"{sample.accession} was rejected and cannot be received.")

    now = now or timezone.now()
    sample.status = SampleStatus.RECEIVED
    sample.received_at = now
    sample.received_by = actor
    sample.updated_by = actor
    sample.save(update_fields=["status", "received_at", "received_by", "updated_by", "updated_at"])

    for request in sample.requests.select_related("order").all():
        request.status = RequestStatus.IN_PROGRESS
        request.started_at = now
        request.updated_by = actor
        request.save(update_fields=["status", "started_at", "updated_by", "updated_at"])
        _advance_order(request.order, OrderStatus.IN_PROGRESS, actor=actor)

    audit.record(
        "lab.sample.received",
        actor=actor,
        object_type="laboratory.Sample",
        object_id=sample.pk,
        patient_id=sample.patient_id,
        changes={
            "accession": sample.accession,
            "minutes_in_transit": sample.minutes_in_transit(),
        },
        **as_audit_kwargs(),
    )
    _broadcast(sample.organisation_id, sample.branch_id, reason="received")
    return sample


@transaction.atomic
def reject(*, sample: Sample, reason: str, note: str = "", actor=None, now=None) -> Sample:
    """
    Refuse a tube, and put its tests back on the collection list.

    The reason is required and comes from a fixed list. See
    ``RejectionReason``.

    A received tube can still be rejected: haemolysis is usually seen when
    the sample is spun, which is after booking in. What cannot be rejected
    is a tube that was already rejected.
    """
    if sample.status == SampleStatus.REJECTED:
        raise WorklistError(f"{sample.accession} has already been rejected.")
    if reason not in RejectionReason.values:
        raise WorklistError("Choose a reason for rejecting the sample.")
    if reason == RejectionReason.OTHER and not note.strip():
        raise WorklistError("Write a sentence saying what was wrong with it.")

    now = now or timezone.now()
    sample.status = SampleStatus.REJECTED
    sample.rejected_at = now
    sample.rejected_by = actor
    sample.rejection_reason = reason
    sample.rejection_note = note.strip()[:300]
    sample.updated_by = actor
    sample.save(
        update_fields=[
            "status",
            "rejected_at",
            "rejected_by",
            "rejection_reason",
            "rejection_note",
            "updated_by",
            "updated_at",
        ]
    )

    # The tests go back to waiting, and keep count. The sample keeps its own
    # snapshot of what it was for, so the rejection log still reads.
    for request in sample.requests.all():
        request.sample = None
        request.status = RequestStatus.AWAITING_COLLECTION
        request.started_at = None
        request.rejection_count += 1
        request.updated_by = actor
        request.save(
            update_fields=[
                "sample",
                "status",
                "started_at",
                "rejection_count",
                "updated_by",
                "updated_at",
            ]
        )

    audit.record(
        "lab.sample.rejected",
        actor=actor,
        object_type="laboratory.Sample",
        object_id=sample.pk,
        patient_id=sample.patient_id,
        changes={
            "accession": sample.accession,
            "reason": reason,
            "note": sample.rejection_note,
            "panels": sample.panel_names,
        },
        flags=["patient_safety"] if sample.is_serious_rejection else None,
        **as_audit_kwargs(),
    )
    _broadcast(sample.organisation_id, sample.branch_id, reason="rejected")
    return sample


@transaction.atomic
def cancel_request(*, request: LabRequest, reason: str, actor=None) -> LabRequest:
    """
    Take a test off the laboratory's list.

    Only before the bench has it. Once a tube has been received somebody is
    part way through the work, and the honest thing is to let it finish and
    let the result stand unused — a cancellation at that point would throw
    away work the patient has already been stuck for.
    """
    if request.status in (RequestStatus.IN_PROGRESS, RequestStatus.CANCELLED):
        raise WorklistError("That test is already with the laboratory. It cannot be cancelled now.")
    if not reason.strip():
        raise WorklistError("Say why the test is being cancelled.")

    request.status = RequestStatus.CANCELLED
    request.cancelled_at = timezone.now()
    request.cancel_reason = reason.strip()[:300]
    request.sample = None
    request.updated_by = actor
    request.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancel_reason",
            "sample",
            "updated_by",
            "updated_at",
        ]
    )
    _broadcast(request.organisation_id, request.branch_id, reason="cancelled")
    return request


def _advance_order(order: Order, status: str, *, actor=None) -> None:
    """
    Move the doctor's order along, and never let that break the laboratory.

    The order's own rules about what may follow what live in
    ``orders.service.advance``. An order the doctor cancelled while the tube
    was in transit will refuse to advance, and that refusal must not stop the
    sample being booked in: the tube is in the building either way, and the
    laboratory's record of it is the thing that matters here.
    """
    try:
        advance(order=order, status=status, actor=actor)
    except OrderError as exc:
        logger.info("order %s not advanced to %s: %s", order.pk, status, exc)


# ---------------------------------------------------------------------------
# Reading the worklist
# ---------------------------------------------------------------------------

#: Priority order on the bench. Stat first, then urgent, then by promise.
PRIORITY_RANK = {"stat": 0, "urgent": 1, "routine": 2}


def _requests(branch_id, *, statuses: tuple[str, ...]):
    return (
        LabRequest.objects.filter(branch_id=branch_id, status__in=statuses)
        .select_related("panel__service", "patient", "sample", "order")
        .order_by("due_at")
    )


def awaiting_collection(branch_id) -> list[LabRequest]:
    """Tests with no tube yet, including redraws after a rejection."""
    return list(_requests(branch_id, statuses=(RequestStatus.AWAITING_COLLECTION,)))


def in_laboratory(branch_id) -> list[LabRequest]:
    """
    Tests whose tube the laboratory has booked in.

    Deliberately not the tubes that have merely been drawn. The screen puts
    "on the way" and "at the laboratory" side by side, and a tube in a porter's
    hand is in exactly one of those two places. Counting it in both is how a
    scientist comes to believe a sample is on the bench when it is still in a
    corridor, which is the misunderstanding that ends in a doctor waiting on a
    result nobody has started.
    """
    return list(
        _requests(
            branch_id,
            statuses=(
                RequestStatus.RECEIVED,
                RequestStatus.IN_PROGRESS,
            ),
        )
    )


def samples_in_transit(branch_id) -> list[Sample]:
    """Tubes drawn but not yet booked in. The laboratory's own inbox."""
    return list(
        Sample.objects.filter(branch_id=branch_id, status=SampleStatus.COLLECTED)
        .select_related("patient")
        .order_by("collected_at")
    )


def rejections(branch_id, *, since=None, limit: int = 50) -> list[Sample]:
    """
    The rejection log, newest first.

    A log rather than a list of problems to clear: the point of it is the
    pattern over a month, not the individual tube, which has already been
    redrawn by the time anybody reads this.
    """
    rows = Sample.objects.filter(branch_id=branch_id, status=SampleStatus.REJECTED)
    if since is not None:
        rows = rows.filter(rejected_at__gte=since)
    return list(rows.select_related("patient").order_by("-rejected_at")[:limit])


def sort_key(request: LabRequest):
    """Stat first, then urgent, then whatever was promised soonest."""
    return (PRIORITY_RANK.get(request.priority, 3), request.due_at)


def counts(branch_id, *, now=None) -> dict[str, int]:
    """
    The numbers on the tabs, in one pass.

    Counted in Python over the open requests rather than as five database
    queries: the open list at a private hospital is tens of rows, not
    thousands, and the overdue count needs ``is_overdue`` anyway.
    """
    now = now or timezone.now()
    waiting = awaiting_collection(branch_id)
    working = in_laboratory(branch_id)
    # Every test still open, not only the two the tabs show. A tube that was
    # drawn an hour ago and is still somewhere between the ward and the
    # bench is the most overdue thing in the building, and it is in neither
    # of those lists.
    open_requests = _requests(branch_id, statuses=OPEN_REQUEST_STATUSES)
    return {
        "awaiting_collection": len(waiting),
        "redraws": sum(1 for request in waiting if request.rejection_count),
        "in_transit": len(samples_in_transit(branch_id)),
        "in_laboratory": len(working),
        "overdue": sum(1 for request in open_requests if request.is_overdue(now=now)),
    }


# ---------------------------------------------------------------------------
# The live board
# ---------------------------------------------------------------------------


def worklist_group(organisation_id, branch_id) -> str:
    return f"org.{organisation_id}.branch.{branch_id}.lab"


def _broadcast(organisation_id, branch_id, *, reason: str) -> None:
    """
    Nudge every open laboratory screen to re-read the worklist.

    A nudge rather than a delta. The worklist is tens of rows, each screen
    filters it differently — a nurse watches the collection list, the bench
    watches the inbox — and re-reading is both simpler and correct after a
    reconnect. It is sent on commit, so nothing is announced that a rolled
    back transaction then un-does.

    Never allowed to break the caller. Redis being down should stop the
    screens updating, not stop blood being drawn.
    """

    def send() -> None:
        layer = get_channel_layer()
        if layer is None:  # pragma: no cover - only without a channel layer
            return
        try:
            async_to_sync(layer.group_send)(
                worklist_group(organisation_id, branch_id),
                {"type": "broadcast", "event": "lab", "reason": reason},
            )
        except Exception:  # see the docstring
            logger.warning("could not broadcast the laboratory worklist", exc_info=True)

    transaction.on_commit(send)
