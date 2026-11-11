"""
Drafting a basket of orders, checking it, and signing it.

The shape of this module is the shape of a consultation. A doctor adds
things to a basket while they are thinking — bloods, an antibiotic,
something for the fever — and then signs once. So:

* ``draft`` adds one line and **checks it immediately**, returning the
  warnings with it, so the screen can show the allergy against the drug
  rather than in a dialog three clicks later.
* ``sign`` takes the whole basket. It re-runs every check at the moment of
  signing, because the basket may have been open for ten minutes and
  somebody may have recorded an allergy in that time, and it refuses the
  whole basket if anything in it is blocked and unexplained.
* Nothing downstream sees an order until it is signed. A draft is a
  thought, not an instruction.

Cancelling keeps the row and the reason. In a hospital this is not
bookkeeping: an order that vanishes is an order the patient still gets
charged for.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.catalogue.pricing import PriceNotFound, resolve_price
from apps.journey.models import Encounter
from apps.orders import safety
from apps.orders.models import (
    STOPPABLE,
    Order,
    OrderKind,
    OrderPriority,
    OrderStatus,
    Referral,
)
from apps.patients.models import Allergy

logger = logging.getLogger("curavia.orders")


class OrderError(Exception):
    """Refused. The message is shown to the clinician as written."""


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


def _price_for(service, branch_id) -> tuple[int | None, str]:
    """What this is expected to cost, or why that could not be answered."""
    if service is None:
        return None, "Not on the price list, so the cashier will price it."
    try:
        resolved = resolve_price(service_id=service.pk, branch_id=branch_id)
    except PriceNotFound:
        # Never zero. A service that silently costs nothing is how a hospital
        # gives away a CT scan for a month before anybody notices.
        return None, "No price is set for this yet."
    return resolved.amount_kobo, ""


def check(
    *,
    encounter: Encounter,
    kind: str,
    name: str,
    exclude_id=None,
) -> list[safety.OrderWarning]:
    """
    Everything wrong with adding this line, right now.

    Public because the screen calls it while the doctor is still choosing,
    and because ``sign`` runs it again over the whole basket — the same
    function both times, so what the doctor was shown and what the server
    enforced cannot drift apart.

    ``exclude_id`` is the order being checked, when there is one. Re-checking
    a line that is already saved otherwise finds itself and says it has
    already been ordered, which is both false and the fastest way to teach
    somebody that the duplicate warning means nothing.
    """
    warnings: list[safety.OrderWarning] = []

    if kind == OrderKind.MEDICATION:
        allergies = list(Allergy.objects.filter(patient_id=encounter.patient_id, is_active=True))
        warnings += safety.allergy_warnings(drug_name=name, allergies=allergies)

    others = Order.objects.filter(
        encounter=encounter,
        kind=kind,
        status__in=(OrderStatus.DRAFT, OrderStatus.ORDERED, OrderStatus.IN_PROGRESS),
    )
    if exclude_id is not None:
        others = others.exclude(pk=exclude_id)
    already = list(others.values_list("name", flat=True))
    duplicate = safety.duplicate_warning(name=name, existing_names=already)
    if duplicate is not None:
        warnings.append(duplicate)

    return warnings


@transaction.atomic
def draft(
    *,
    encounter: Encounter,
    kind: str,
    name: str = "",
    service=None,
    priority: str = OrderPriority.ROUTINE,
    instructions: str = "",
    reason: str = "",
    dose: str = "",
    route: str = "",
    frequency: str = "",
    duration: str = "",
    quantity: int | None = None,
    actor=None,
) -> Order:
    """Add one line to the basket, checked."""
    if kind not in OrderKind.values:
        raise OrderError(f"There is no kind of order called {kind!r}.")
    if priority not in OrderPriority.values:
        raise OrderError(f"There is no priority called {priority!r}.")
    if encounter.status != Encounter.Status.OPEN:
        raise OrderError("That visit is closed.")

    name = (name or (service.name if service is not None else "")).strip()
    if not name:
        raise OrderError("Say what is being ordered.")

    estimate, note = _price_for(service, encounter.branch_id)
    warnings = check(encounter=encounter, kind=kind, name=name)

    order = Order(
        organisation_id=encounter.organisation_id,
        branch_id=encounter.branch_id,
        encounter=encounter,
        patient_id=encounter.patient_id,
        kind=kind,
        service=service,
        name=name[:200],
        instructions=instructions.strip()[:300],
        reason=reason.strip()[:300],
        priority=priority,
        dose=dose.strip()[:60],
        route=route.strip()[:40],
        frequency=frequency.strip()[:60],
        duration=duration.strip()[:60],
        quantity=quantity,
        estimate_kobo=estimate,
        estimate_note=note,
        warnings=[warning.as_dict() for warning in warnings],
        created_by=actor,
    )
    order.full_clean(exclude=["organisation", "branch", "encounter", "patient", "service"])
    order.save()
    return order


@transaction.atomic
def update_draft(*, order: Order, actor=None, **fields) -> Order:
    """
    Change a line that has not been signed yet.

    Only a draft. Once an order has gone to the laboratory, changing its
    wording would mean the paper in the laboratory and the record no longer
    say the same thing — that is a cancellation and a new order.
    """
    if not order.is_draft:
        raise OrderError("That order has already been sent. Cancel it and order again.")

    editable = {
        "name",
        "instructions",
        "reason",
        "priority",
        "dose",
        "route",
        "frequency",
        "duration",
        "quantity",
    }
    changed = []
    for field, value in fields.items():
        if field in editable and value is not None:
            setattr(order, field, value)
            changed.append(field)
    if not changed:
        return order

    order.warnings = [
        warning.as_dict()
        for warning in check(
            encounter=order.encounter,
            kind=order.kind,
            name=order.name,
            exclude_id=order.pk,
        )
    ]
    order.updated_by = actor
    order.full_clean(exclude=["organisation", "branch", "encounter", "patient", "service"])
    order.save(update_fields=[*changed, "warnings", "updated_by", "updated_at"])
    return order


def basket(encounter: Encounter) -> list[Order]:
    return list(
        Order.objects.filter(encounter=encounter, status=OrderStatus.DRAFT).order_by("created_at")
    )


def basket_total(orders) -> int:
    return sum(order.estimate_kobo or 0 for order in orders)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


@transaction.atomic
def sign(*, encounter: Encounter, actor=None, override_reason: str = "") -> list[Order]:
    """
    Send the whole basket.

    Every check runs again here. The basket may have been open for ten
    minutes while a nurse recorded an allergy, and the screen's copy of the
    warnings is from whenever the line was added — so what is enforced is
    what is true now, not what the browser remembers.

    A blocked order stops the **whole basket**, not just itself. The orders
    in a consultation are one plan: half of a plan going through while the
    doctor is still deciding about the antibiotic is how a patient gets
    bloods taken for a review that does not happen.
    """
    drafts = basket(encounter)
    if not drafts:
        raise OrderError("There is nothing to sign.")

    blocked: list[str] = []
    for order in drafts:
        warnings = check(encounter=encounter, kind=order.kind, name=order.name, exclude_id=order.pk)
        order.warnings = [warning.as_dict() for warning in warnings]
        if any(warning.tone == "critical" for warning in warnings) and not override_reason.strip():
            blocked.append(order.name)

    if blocked:
        raise OrderError(
            "This cannot be signed as it is: "
            + ", ".join(blocked)
            + ". Remove it, change the medicine, or write down why you are going ahead."
        )

    now = timezone.now()
    for order in drafts:
        order.status = OrderStatus.ORDERED
        order.ordered_at = now
        order.ordered_by = actor
        order.override_reason = override_reason.strip()[:300] if order.blocking_warnings else ""
        order.updated_by = actor
        order.save(
            update_fields=[
                "status",
                "ordered_at",
                "ordered_by",
                "warnings",
                "override_reason",
                "updated_by",
                "updated_at",
            ]
        )

    logger.info("%s order(s) signed on encounter %s", len(drafts), encounter.pk)
    return drafts


@transaction.atomic
def cancel(*, order: Order, reason: str, actor=None) -> Order:
    """
    Stop an order. Keeps the row and the reason.

    A draft needs no reason. Nobody has been told to do anything yet, so
    taking it off the basket is the doctor changing their mind, and a
    question asked at that moment is answered with a full stop — which then
    devalues the answer on the orders that have really gone out. Once an
    order is sent, a department may already be acting on it, so stopping it
    is an event in the record and has to say why.
    """
    if order.status not in STOPPABLE:
        raise OrderError("That order is finished. It cannot be stopped now.")
    if order.status != OrderStatus.DRAFT and not reason.strip():
        raise OrderError("Say why the order is being stopped.")

    order.status = OrderStatus.CANCELLED
    order.cancelled_at = timezone.now()
    order.cancelled_by = actor
    order.cancel_reason = reason.strip()[:300]
    order.updated_by = actor
    order.save(
        update_fields=[
            "status",
            "cancelled_at",
            "cancelled_by",
            "cancel_reason",
            "updated_by",
            "updated_at",
        ]
    )
    return order


@transaction.atomic
def advance(*, order: Order, status: str, actor=None) -> Order:
    """
    Move an order along: being done, then finished.

    The departments that do the work drive this in milestone 7. It lives
    here so that the rules about what may follow what are in one place
    rather than in each department's own screen.
    """
    allowed: dict[str, tuple[str, ...]] = {
        OrderStatus.ORDERED: (OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED),
        OrderStatus.IN_PROGRESS: (OrderStatus.COMPLETED,),
    }
    if status not in allowed.get(order.status, ()):
        raise OrderError(
            f"An order that is {order.get_status_display().lower()} cannot become "
            f"{OrderStatus(status).label.lower()}."
        )

    order.status = status
    if status == OrderStatus.COMPLETED:
        order.completed_at = timezone.now()
    order.updated_by = actor
    order.save(update_fields=["status", "completed_at", "updated_by", "updated_at"])
    return order


def for_encounter(encounter: Encounter) -> list[Order]:
    """
    What this visit shows on screen.

    A sent order that was stopped stays on the list: an order that vanishes
    is an order the patient is still charged for, and the ward needs to see
    that it was stopped rather than simply not find it. A draft taken off
    the basket is different — nobody was ever told to do anything, so it is
    the doctor changing their mind, and leaving it on screen means every
    visit ends carrying a list of things that did not happen, each with its
    warning still showing. The row itself is kept; it just stops being part
    of the visit.
    """
    return list(
        Order.objects.filter(encounter=encounter)
        .exclude(status=OrderStatus.CANCELLED, ordered_at__isnull=True)
        .select_related("ordered_by", "service")
        .order_by("status", "created_at")
    )


def for_patient(patient, *, limit: int = 100) -> list[Order]:
    return list(
        Order.objects.filter(patient=patient)
        .exclude(status=OrderStatus.DRAFT)
        .select_related("ordered_by")
        .order_by("-ordered_at")[:limit]
    )


# ---------------------------------------------------------------------------
# Referrals
# ---------------------------------------------------------------------------


@transaction.atomic
def refer(
    *,
    encounter: Encounter,
    kind: str,
    reason: str,
    to_department=None,
    to_facility: str = "",
    to_clinician: str = "",
    speciality: str = "",
    urgency: str = Referral.Urgency.ROUTINE,
    letter: str = "",
    actor=None,
    send: bool = True,
) -> Referral:
    """
    Send the patient to somebody else.

    Sent immediately by default. A referral held as a draft is a referral
    the patient leaves without, and the one thing a referral has to do is
    leave the building with them.
    """
    if kind not in Referral.Kind.values:
        raise OrderError(f"There is no kind of referral called {kind!r}.")
    if encounter.status != Encounter.Status.OPEN:
        raise OrderError("That visit is closed.")

    referral = Referral(
        organisation_id=encounter.organisation_id,
        branch_id=encounter.branch_id,
        encounter=encounter,
        patient_id=encounter.patient_id,
        kind=kind,
        urgency=urgency if urgency in Referral.Urgency.values else Referral.Urgency.ROUTINE,
        to_department=to_department,
        to_facility=to_facility.strip()[:160],
        to_clinician=to_clinician.strip()[:160],
        speciality=speciality.strip()[:80],
        reason=reason.strip()[:300],
        letter=letter.strip(),
        created_by=actor,
    )
    if send:
        referral.status = Referral.Status.SENT
        referral.referred_at = timezone.now()
        referral.referred_by = actor

    referral.full_clean(
        exclude=["organisation", "branch", "encounter", "patient", "to_department", "referred_by"]
    )
    referral.save()
    return referral


@transaction.atomic
def respond_to_referral(*, referral: Referral, status: str, note: str = "", actor=None) -> Referral:
    """The other end answers: accepted, declined, or the patient was seen."""
    allowed = (
        Referral.Status.ACCEPTED,
        Referral.Status.DECLINED,
        Referral.Status.COMPLETED,
        Referral.Status.CANCELLED,
    )
    if status not in allowed:
        raise OrderError(f"A referral cannot be marked {status!r}.")
    if referral.status in (Referral.Status.COMPLETED, Referral.Status.CANCELLED):
        raise OrderError("That referral is already closed.")
    if status == Referral.Status.DECLINED and not note.strip():
        raise OrderError("Say why the referral is being declined.")

    referral.status = status
    referral.responded_at = timezone.now()
    referral.response_note = note.strip()[:300]
    referral.updated_by = actor
    referral.save(
        update_fields=["status", "responded_at", "response_note", "updated_by", "updated_at"]
    )
    return referral


def referrals_for(encounter: Encounter) -> list[Referral]:
    return list(
        Referral.objects.filter(encounter=encounter)
        .select_related("to_department", "referred_by")
        .order_by("-created_at")
    )


def inbox(department) -> list[Referral]:
    """What has been sent to one department and not yet dealt with."""
    return list(
        Referral.objects.filter(
            to_department=department,
            status__in=(Referral.Status.SENT, Referral.Status.ACCEPTED),
        )
        .select_related("patient", "referred_by")
        .order_by("-urgency", "referred_at")
    )


__all__ = [
    "OrderError",
    "advance",
    "basket",
    "basket_total",
    "cancel",
    "check",
    "draft",
    "for_encounter",
    "for_patient",
    "inbox",
    "refer",
    "referrals_for",
    "respond_to_referral",
    "sign",
    "update_draft",
]
