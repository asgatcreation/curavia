"""
Core endpoints.

Small on purpose. Milestone 2 ships the endpoints that prove the foundations
work end to end — who am I, what may I do, which branch am I in, show me the
audit log, is the chain intact — and nothing else. Every later module plugs
into the same four-layer permission check these use.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.core import audit
from apps.core.audit import AuditEvent, ChainVerificationError
from apps.core.models import Branch, Department
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs

from .base import (
    CuraviaAPIView,
    CuraviaReadOnlyModelViewSet,
    SchemaSafeQuerysetMixin,
)
from .filters import AuditEventFilterSet, DepartmentFilterSet
from .pagination import AuditPagination
from .serializers import (
    AuditEventSerializer,
    BranchSerializer,
    BranchSwitchSerializer,
    ChainVerificationSerializer,
    DepartmentSerializer,
    MeSerializer,
    ModuleSerializer,
    PermissionCatalogueSerializer,
    module_catalogue,
    permission_catalogue,
)
from .types import CuraviaRequest

# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


@csrf_exempt
@never_cache
def healthz(request: HttpRequest) -> JsonResponse:
    """
    Liveness. Touches nothing.

    Deliberately free of database and Redis calls: this is the endpoint
    UptimeRobot hits every five minutes to stop Render's free tier sleeping
    (docs/00-decisions.md §3), so it must be cheap and must not fail because a
    dependency is briefly slow. Note it is NOT /robots.txt — Render answers
    that one itself without waking the service, which would make the whole
    keep-alive silently useless.
    """
    return JsonResponse({"status": "ok", "service": "curavia"})


@csrf_exempt
@never_cache
def readyz(request: HttpRequest) -> JsonResponse:
    """
    Readiness. Checks the things a request actually needs.

    Reports per-dependency so a failing deploy says *what* is wrong rather than
    just "unhealthy".
    """
    checks: dict[str, Any] = {}
    ok = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - depends on the environment
        checks["database"] = f"error: {type(exc).__name__}"
        ok = False

    try:
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        checks["channel_layer"] = "ok" if layer is not None else "not configured"
    except Exception as exc:  # pragma: no cover
        checks["channel_layer"] = f"error: {type(exc).__name__}"
        ok = False

    checks["rls_enabled"] = settings.CURAVIA["ENABLE_RLS"]
    return JsonResponse(
        {"status": "ok" if ok else "degraded", "checks": checks},
        status=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


# --------------------------------------------------------------------------
# Me
# --------------------------------------------------------------------------


class MeView(CuraviaAPIView):
    """
    Everything the staff app needs on boot.

    One request rather than five, because a nurse on a tablet over a 3G
    connection should not wait for a waterfall before the first screen paints.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: MeSerializer})
    def get(self, request: CuraviaRequest) -> Response:
        user = request.user
        organisation = request.organisation
        branch = None
        if request.membership and getattr(request, "organisation", None):
            from apps.core.tenancy import current_branch_id

            branch_id = current_branch_id()
            if branch_id:
                branch = Branch.objects.filter(pk=branch_id).first()

        memberships = [
            {
                "organisation_id": membership.organisation_id,
                "organisation_name": membership.organisation.name,
                "job_title": membership.job_title,
                "roles": [role.name for role in membership.roles.all()],
                "branches": list(membership.branches.all()),
            }
            for membership in user.live_memberships()
        ]

        payload = {
            "id": user.pk,
            "email": user.email,
            "full_name": user.full_name,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "is_platform_staff": user.is_platform_staff,
            "has_mfa": self._has_mfa(user),
            "organisation": organisation,
            "active_branch": branch,
            "memberships": memberships,
            "permissions": sorted(request.tenant_permissions),
            "modules": sorted(request.available_modules),
        }
        return Response(MeSerializer(payload, context={"request": request}).data)

    @staticmethod
    def _has_mfa(user: Any) -> bool:
        try:
            from allauth.mfa.models import Authenticator

            return Authenticator.objects.filter(user=user).exists()
        except Exception:  # pragma: no cover - allauth.mfa optional at boot
            return False


class SwitchBranchView(CuraviaAPIView):
    """
    Move between the branches this person works at.

    The switch is validated against their membership, remembered for next
    login, and audited — "who was working in which branch when" is a question
    an auditor will ask.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=BranchSwitchSerializer,
        responses={200: BranchSerializer, 400: OpenApiResponse(description="Not your branch")},
    )
    def post(self, request: CuraviaRequest) -> Response:
        serializer = BranchSwitchSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        branch_id = serializer.validated_data["branch"]

        branch = Branch.objects.get(pk=branch_id)
        request.user.last_organisation_id = branch.organisation_id
        request.user.last_branch_id = branch.pk
        request.user.save(update_fields=["last_organisation_id", "last_branch_id"])

        audit.record(
            "branch.switch",
            actor=request.user,
            object_type="Branch",
            object_id=branch.pk,
            changes={"branch": branch.name},
            **as_audit_kwargs(),
        )
        return Response(BranchSerializer(branch).data)


class ModuleListView(CuraviaAPIView):
    """The module catalogue, with which ones are live for this hospital."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ModuleSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        return Response(module_catalogue(request.available_modules))


class PermissionCatalogueView(CuraviaAPIView):
    """
    Every permission code, for the role editor.

    Generated from the same registry the API enforces with, so a hospital
    building a custom role can never grant something that does not exist.
    """

    permission_classes = [HasPermission]
    required_permissions = ["role.manage"]

    @extend_schema(responses={200: PermissionCatalogueSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        return Response(permission_catalogue())


# --------------------------------------------------------------------------
# Branches and departments
# --------------------------------------------------------------------------


class BranchViewSet(SchemaSafeQuerysetMixin, CuraviaReadOnlyModelViewSet):
    """Read-only here; branches are created by the setup wizard at milestone 4."""

    # Declared for schema generation only. drf-spectacular introspects this
    # attribute with no request and therefore no tenant scope, where the
    # guard in apps.core.tenancy would — correctly — raise. `.none()` on the
    # unscoped manager gives it the model without touching any row.
    queryset = Branch.unscoped.none()
    serializer_class = BranchSerializer
    permission_classes = [HasPermission]
    required_permissions = ["org.settings.view"]

    def get_tenant_queryset(self):  # type: ignore[no-untyped-def]
        # The manager is already scoped to this hospital and would raise if it
        # were not — see apps.core.tenancy.
        return Branch.objects.live().order_by("name")


class DepartmentViewSet(SchemaSafeQuerysetMixin, CuraviaReadOnlyModelViewSet):
    queryset = Department.unscoped.none()  # schema generation only; see above
    serializer_class = DepartmentSerializer
    permission_classes = [HasPermission]
    required_permissions = ["org.settings.view"]
    filterset_class = DepartmentFilterSet

    def get_tenant_queryset(self):  # type: ignore[no-untyped-def]
        return Department.objects.live().in_current_branch().order_by("name")


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------


class AuditEventViewSet(SchemaSafeQuerysetMixin, CuraviaReadOnlyModelViewSet):
    """
    The audit log, for the auditor and the medical director.

    Two details worth noticing:

    * It is filtered by ``organisation_id`` explicitly. ``AuditEvent`` is not a
      tenant-scoped model — platform events have no hospital — so this is one
      of the few places the filter is written by hand, and
      ``tests/isolation`` asserts it holds.
    * Reading it is itself audited. An auditor's own access is on the record,
      which is the only way the log means anything.
    """

    queryset = AuditEvent.objects.none()  # schema generation only; see above
    serializer_class = AuditEventSerializer
    permission_classes = [HasPermission]
    required_permissions = ["audit.view"]
    pagination_class = AuditPagination
    filterset_class = AuditEventFilterSet

    def get_tenant_queryset(self):  # type: ignore[no-untyped-def]
        from apps.core.tenancy import current_organisation_id

        return (
            AuditEvent.objects.filter(  # type: ignore[misc]
                organisation_id=current_organisation_id()
            )
            .select_related("actor")
            .order_by("-at", "-sequence")
        )

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        response = super().list(request, *args, **kwargs)
        audit.record(
            "audit.view",
            actor=request.user,
            object_type="AuditEvent",
            changes={"filters": dict(request.query_params)},
            flags=["bulk"],
            **as_audit_kwargs(),
        )
        return response

    @extend_schema(responses={200: ChainVerificationSerializer})
    @action(detail=False, methods=["get"], url_path="verify", permission_classes=[HasPermission])
    def verify(self, request: CuraviaRequest) -> Response:
        """
        Recompute the whole chain and report whether it holds.

        A tampered or deleted row is reported with the exact sequence number,
        which is the difference between "we have an audit log" and "we can
        prove our audit log is complete".
        """
        from apps.core.tenancy import current_organisation_id

        organisation_id = current_organisation_id()
        try:
            count, head = audit.verify_chain(organisation_id)
        except ChainVerificationError as exc:
            payload = {
                "valid": False,
                "event_count": 0,
                "head_hash": "",
                "problem": exc.problem,
                "broken_at_sequence": exc.sequence,
            }
            return Response(ChainVerificationSerializer(payload).data)

        payload = {
            "valid": True,
            "event_count": count,
            "head_hash": head,
            "problem": None,
            "broken_at_sequence": None,
        }
        return Response(ChainVerificationSerializer(payload).data)

    @property
    def permission_map(self) -> dict[str, Sequence[str]]:
        # Consulted by HasPermission; `verify` needs a different code from the
        # list action, which is the point of per-action mapping.
        return {"verify": ["audit.verify"]}
