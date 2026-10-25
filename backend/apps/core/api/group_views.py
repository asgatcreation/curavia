"""
The owner's view across branches.

**It reports only what the system can actually measure today, and says so
about the rest.** Patients, queues, admissions and money arrive at
milestones 5, 8 and 10, so the tiles that depend on them have no source yet.
They are returned as `pending` — named, with the milestone they arrive at —
rather than filled with a plausible number.

That is not caution, it is the whole point of the screen. An owner looks at
this to decide whether to put a second doctor on a Tuesday. A figure that
looks real and is not would be worse than no screen at all, and once one tile
is invented nobody can trust the ones that are not.

No patient is named anywhere in this response. The owner role can see counts,
beds, staff and alerts across every branch without opening a record, and this
endpoint simply has nothing to leak.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import Invitation, StaffMembership
from apps.catalogue.models import Payer, Service
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.models import Bed, Branch, Department
from apps.core.permissions import HasPermission
from apps.core.setup_steps import completed_steps, setup_is_complete

#: Tiles the dashboard has a place for and the system cannot yet fill. The
#: client renders these as "waiting for milestone N" rather than as a number.
PENDING_MEASURES: tuple[dict[str, str], ...] = (
    {
        "key": "waiting",
        "label": "Patients waiting",
        "milestone": "milestone 5",
        "note": "Needs the live queue and journey board.",
    },
    {
        "key": "longest_wait",
        "label": "Longest wait",
        "milestone": "milestone 5",
        "note": "Needs the live queue and journey board.",
    },
    {
        "key": "occupancy",
        "label": "Bed occupancy",
        "milestone": "milestone 10",
        "note": "Beds are counted now; who is in them needs admissions.",
    },
    {
        "key": "billed_today",
        "label": "Billed today",
        "milestone": "milestone 8",
        "note": "Needs billing and the cashier.",
    },
    {
        "key": "outstanding",
        "label": "Outstanding",
        "milestone": "milestone 8",
        "note": "Needs billing and HMO claims.",
    },
)


class GroupSummarySerializer(serializers.Serializer):
    """Documented for the schema; assembled by hand in the view."""

    as_of = serializers.DateTimeField()
    organisation = serializers.DictField()
    totals = serializers.DictField()
    branches = serializers.ListField(child=serializers.DictField())
    alerts = serializers.ListField(child=serializers.DictField())
    pending = serializers.ListField(child=serializers.DictField())


class GroupSummaryView(CuraviaAPIView):
    """Everything the group dashboard shows, in one request."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["reporting.view"]

    @extend_schema(responses={200: GroupSummarySerializer})
    def get(self, request: CuraviaRequest) -> Response:
        organisation = request.organisation
        if organisation is None:
            return Response(
                {"detail": "You are not signed in to a hospital.", "code": "no_organisation"},
                status=400,
            )

        branches = list(
            Branch.objects.annotate(
                department_count=Count("department_set", distinct=True),
                ward_count=Count(
                    "department_set",
                    filter=Q(department_set__kind=Department.Kind.WARD),
                    distinct=True,
                ),
                bed_count=Count("bed_set", distinct=True),
            ).order_by("-is_main", "name")
        )

        # Staff per branch, counted from the membership's branches rather than
        # from a column: somebody who works at two sites is on duty at both,
        # and a per-branch column would have to be kept in step with the M2M.
        staff_by_branch: dict[Any, int] = {}
        memberships = StaffMembership.objects.filter(is_active=True).prefetch_related("branches")
        for membership in memberships:
            for branch in membership.branches.all():
                staff_by_branch[branch.pk] = staff_by_branch.get(branch.pk, 0) + 1

        without_mfa = _staff_without_mfa(memberships)

        totals = {
            "branches": len(branches),
            "departments": Department.objects.count(),
            "beds": Bed.objects.filter(is_active=True).count(),
            "staff_active": memberships.count(),
            "staff_without_mfa": without_mfa,
            "services_priced": Service.objects.filter(is_active=True).count(),
            "payers": Payer.objects.exclude(kind=Payer.Kind.SELF).filter(is_active=True).count(),
            "invitations_open": Invitation.objects.filter(
                status=Invitation.Status.PENDING, expires_at__gt=timezone.now()
            ).count(),
        }

        return Response(
            {
                "as_of": timezone.now(),
                "organisation": {
                    "name": organisation.name,
                    "size_mode": organisation.size_mode,
                    "status": organisation.status,
                    "setup_complete": setup_is_complete(organisation),
                },
                "totals": totals,
                "branches": [
                    {
                        "id": str(branch.pk),
                        "name": branch.name,
                        "code": branch.code,
                        "is_main": branch.is_main,
                        "state": branch.state,
                        "departments": branch.department_count,  # type: ignore[attr-defined]
                        "wards": branch.ward_count,  # type: ignore[attr-defined]
                        "beds": branch.bed_count,  # type: ignore[attr-defined]
                        "staff_active": staff_by_branch.get(branch.pk, 0),
                    }
                    for branch in branches
                ],
                "alerts": _alerts(organisation, branches, totals),
                "pending": list(PENDING_MEASURES),
            }
        )


def _staff_without_mfa(memberships) -> int:
    """
    How many active staff have not enrolled an authenticator.

    One query for the whole set rather than one per person: this is a
    dashboard, and a hospital with two hundred staff would otherwise make two
    hundred round trips to draw one number.
    """
    try:
        from allauth.mfa.models import Authenticator
    except Exception:  # pragma: no cover - allauth.mfa optional at boot
        return 0

    user_ids = set(memberships.values_list("user_id", flat=True))
    if not user_ids:
        return 0
    enrolled = set(
        Authenticator.objects.filter(user_id__in=user_ids).values_list("user_id", flat=True)
    )
    return len(user_ids - enrolled)


def _alerts(organisation, branches, totals: dict[str, int]) -> list[dict[str, str]]:
    """
    The things somebody should actually do something about.

    Only conditions the system can see today. Stock levels, expiring licences
    and bed occupancy are real alerts a hospital wants and they arrive with
    the modules that measure them — they are not invented here to make the
    list look longer.
    """
    alerts: list[dict[str, str]] = []

    if not setup_is_complete(organisation):
        done = completed_steps(organisation)
        alerts.append(
            {
                "tone": "warning",
                "branch": "",
                "message": (
                    f"Setup is not finished — {len(done)} of 7 steps done. "
                    "Departments, services and prices decide what your staff can do."
                ),
            }
        )

    if totals["staff_without_mfa"]:
        count = totals["staff_without_mfa"]
        alerts.append(
            {
                "tone": "warning",
                "branch": "",
                "message": (
                    f"{count} member{'s' if count != 1 else ''} of staff "
                    f"{'have' if count != 1 else 'has'} not set up two-factor "
                    "authentication."
                ),
            }
        )

    for branch in branches:
        if branch.department_count == 0:  # type: ignore[attr-defined]
            alerts.append(
                {
                    "tone": "warning",
                    "branch": branch.name,
                    "message": "This branch has no departments, so nobody can be seen there.",
                }
            )

    unpriced = (
        Service.objects.filter(is_active=True)
        .annotate(price_count=Count("prices"))
        .filter(price_count=0)
        .count()
    )
    if unpriced:
        alerts.append(
            {
                "tone": "critical",
                "branch": "",
                "message": (
                    f"{unpriced} service{'s' if unpriced != 1 else ''} "
                    f"{'have' if unpriced != 1 else 'has'} no price, so "
                    f"{'they' if unpriced != 1 else 'it'} cannot be billed."
                ),
            }
        )

    if totals["invitations_open"]:
        count = totals["invitations_open"]
        alerts.append(
            {
                "tone": "info",
                "branch": "",
                "message": (
                    f"{count} invitation{'s' if count != 1 else ''} "
                    f"{'have' if count != 1 else 'has'} not been accepted yet."
                ),
            }
        )

    return alerts


__all__ = ["PENDING_MEASURES", "GroupSummaryView"]
