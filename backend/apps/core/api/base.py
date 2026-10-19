"""
The base every API view inherits.

**Why this exists rather than middleware.** Tenant scope was originally
resolved in ``TenantScopeMiddleware``, which reads ``request.user``. That works
for session authentication, because ``AuthenticationMiddleware`` has already
run. It does **not** work for JWT: DRF authenticates inside the view, in
``APIView.initial()``, long after every middleware has finished. So for a JWT
client the middleware saw ``AnonymousUser``, set no scope, and the first
tenant-scoped query in the view raised ``TenantScopeMissing`` — a 500 on a
request that should have worked.

Session auth hid the bug completely, which is the kind of thing a token-based
test suite exists to catch.

So resolution happens here, in ``initial()``, which DRF calls after
``perform_authentication`` and before the handler. ``finalize_response`` puts
the scope back, so a contextvar never outlives the request that set it.

Every view under ``/api/v1/`` must inherit one of these, and
``tests/test_api_base.py`` fails the build if one does not — the same
structural-test approach used for the tenant managers themselves. A view that
quietly forgot the base would work under session auth in development and 500
in production.
"""

from __future__ import annotations

from typing import Any

from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.scope import ScopeDenied, resolve
from apps.core.tenancy import TenantScope, reset_scope, set_scope

ORGANISATION_HEADER = "HTTP_X_CURAVIA_ORGANISATION"
BRANCH_HEADER = "HTTP_X_CURAVIA_BRANCH"


class TenantScopedMixin:
    """
    Resolve the caller's hospital, and attach it to the request.

    After ``initial()`` every handler can rely on:

    * ``request.organisation``        — the Organisation, or None
    * ``request.membership``          — their StaffMembership, or None
    * ``request.tenant_permissions``  — the codes they hold there
    * ``request.available_modules``   — modules in the plan and switched on

    A caller with no membership gets an empty scope rather than an error: a
    patient-portal account and one of Curavia's own staff are both legitimate
    callers who simply hold no hospital data. The permission classes then
    refuse anything that needs a hospital.
    """

    #: Set by initial(), consumed by finalize_response().
    _scope_token: Any = None

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        # Authenticates and checks permissions. Must run first: resolution
        # needs to know who is calling.
        super().initial(request, *args, **kwargs)  # type: ignore[misc]
        self._apply_scope(request)

    def _apply_scope(self, request: Request) -> None:
        request.organisation = None  # type: ignore[attr-defined]
        request.membership = None  # type: ignore[attr-defined]
        request.tenant_permissions = frozenset()  # type: ignore[attr-defined]
        request.available_modules = frozenset()  # type: ignore[attr-defined]

        try:
            resolved = resolve(
                request.user,
                requested_organisation=request.META.get(ORGANISATION_HEADER),
                requested_branch=request.META.get(BRANCH_HEADER),
            )
        except ScopeDenied as exc:
            # 403 with the message from scope.py, which deliberately does not
            # confirm whether the requested hospital exists.
            raise PermissionDenied(str(exc)) from exc

        if resolved is None:
            return

        request.organisation = resolved.organisation  # type: ignore[attr-defined]
        request.membership = resolved.membership  # type: ignore[attr-defined]
        request.tenant_permissions = resolved.permission_codes  # type: ignore[attr-defined]
        request.available_modules = resolved.available_modules  # type: ignore[attr-defined]

        self._scope_token = set_scope(
            TenantScope(
                organisation_id=resolved.organisation.pk,
                branch_id=resolved.branch_id,
                all_branches=resolved.all_branches,
            )
        )

    def finalize_response(self, request: Request, response: Response, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        # Runs on the error path too, because DRF's dispatch() routes
        # exceptions through handle_exception() and then here.
        try:
            return super().finalize_response(request, response, *args, **kwargs)  # type: ignore[misc]
        finally:
            if self._scope_token is not None:
                reset_scope(self._scope_token)
                self._scope_token = None


class CuraviaAPIView(TenantScopedMixin, APIView):
    """A plain endpoint, scoped to one hospital."""


class CuraviaViewSet(TenantScopedMixin, viewsets.ViewSet):
    """A set of actions with no model behind them."""


class CuraviaReadOnlyModelViewSet(TenantScopedMixin, viewsets.ReadOnlyModelViewSet):
    """List and retrieve, scoped to one hospital."""


class CuraviaModelViewSet(TenantScopedMixin, viewsets.ModelViewSet):
    """Full CRUD, scoped to one hospital."""


class SchemaSafeQuerysetMixin:
    """
    Let drf-spectacular introspect a tenant-scoped viewset.

    Schema generation calls ``get_queryset()`` with no request, and therefore
    no hospital in scope, where the guard in ``apps.core.tenancy`` correctly
    raises. drf-spectacular sets ``swagger_fake_view`` while introspecting, so
    this returns the declared, empty ``queryset`` in that case and the real one
    otherwise.

    The alternative — relaxing the guard so unscoped queries return nothing —
    would weaken the protection everywhere to satisfy a documentation tool.
    """

    def get_queryset(self):  # type: ignore[no-untyped-def]
        if getattr(self, "swagger_fake_view", False):
            return self.queryset  # type: ignore[attr-defined]
        return self.get_tenant_queryset()

    def get_tenant_queryset(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError
