"""
Looking a price up.

One function, because every part of the system that charges for anything has
to agree about this: the cashier's screen, the doctor's order basket, an HMO
claim, and a report of what a visit cost. Four implementations of "most
specific price in force" would be four slightly different answers to the same
question.

The precedence, most specific first:

    1. this branch,  this payer
    2. all branches, this payer
    3. this branch,  self-pay
    4. all branches, self-pay

Rules 3 and 4 are the fallback that stops a hospital having to enter nine
hundred rows per HMO. A scheme the hospital has not priced separately is
charged the hospital's own rate, which is what actually happens at the desk.

"In force" is relative to a date, not to now, so a bill raised in March can
still be recomputed in May from the prices that applied in March.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from apps.catalogue.models import Payer, Price


class PriceNotFound(Exception):
    """
    No price, at any level, for this service.

    Raised rather than returning zero. A service that silently costs nothing
    is how a hospital gives away a CT scan for a month before anybody notices.
    """

    def __init__(self, service_id: UUID | str, payer_id: UUID | str | None) -> None:
        self.service_id = service_id
        self.payer_id = payer_id
        super().__init__(
            f"No price is set for service {service_id} (payer {payer_id or 'self-pay'})."
        )


@dataclass(frozen=True, slots=True)
class ResolvedPrice:
    amount_kobo: int
    price_id: UUID
    #: Which rule matched, for the "why am I being charged this" question.
    source: str
    payer_id: UUID
    branch_specific: bool


def _self_pay_payer() -> Payer | None:
    return Payer.objects.filter(kind=Payer.Kind.SELF, is_active=True).first()


def resolve_price(
    *,
    service_id: UUID | str,
    payer_id: UUID | str | None = None,
    branch_id: UUID | str | None = None,
    on: date | None = None,
) -> ResolvedPrice:
    """
    The amount to charge, and where it came from.

    Runs inside the caller's tenant scope: ``Price.objects`` refuses to answer
    without one, so this cannot accidentally price a service against another
    hospital's list.
    """
    when = on or timezone.localdate()

    payer_ids: list[UUID | str] = []
    if payer_id:
        payer_ids.append(payer_id)
    self_pay = _self_pay_payer()
    if self_pay and self_pay.pk not in payer_ids:
        payer_ids.append(self_pay.pk)

    if not payer_ids:
        raise PriceNotFound(service_id, payer_id)

    in_force = Q(effective_from__lte=when) & (
        Q(effective_to__isnull=True) | Q(effective_to__gte=when)
    )

    candidates = list(
        Price.objects.filter(in_force, service_id=service_id, payer_id__in=payer_ids)
        .select_related("payer")
        .order_by("-effective_from")
    )

    # Walked in precedence order rather than sorted by it: four explicit
    # passes over a handful of rows is clearer than a CASE expression, and
    # this is the function everybody will come and read when a bill looks
    # wrong.
    for candidate_payer in payer_ids:
        for branch_specific in (True, False):
            for price in candidates:
                if str(price.payer_id) != str(candidate_payer):
                    continue
                is_branch_row = price.branch_id is not None
                if is_branch_row != branch_specific:
                    continue
                if branch_specific and str(price.branch_id) != str(branch_id):
                    continue
                return ResolvedPrice(
                    amount_kobo=price.amount_kobo,
                    price_id=price.pk,
                    source=_describe(price, requested_payer=payer_id),
                    payer_id=price.payer_id,
                    branch_specific=is_branch_row,
                )

    raise PriceNotFound(service_id, payer_id)


def _describe(price: Price, *, requested_payer: UUID | str | None) -> str:
    where = "this branch" if price.branch_id else "all branches"
    if requested_payer and str(price.payer_id) != str(requested_payer):
        return f"self-pay rate for {where} (this payer has no price of its own)"
    return f"{price.payer.name} rate for {where}"
