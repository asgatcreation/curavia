"""
Tenant scope for Django-rendered requests, and the request context.

**The API does not come through here.** Tenant resolution for ``/api/`` lives
in ``apps.core.api.base.TenantScopedMixin``, because DRF authenticates inside
the view — after every middleware has run — so a JWT caller looks anonymous
from here. Trying to do it in both places gave a scope that was set for
session clients and missing for token clients, which is the worst of both.

What is left for this middleware is the Django side: the admin and the
marketing site, which use session authentication and where
``AuthenticationMiddleware`` has already populated ``request.user``. Both call
the same ``apps.core.scope.resolve``, so the two paths cannot drift.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone

from .modules import ALL_MODULE_CODES
from .scope import ScopeDenied, resolve
from .tenancy import TenantScope, reset_scope, set_scope

logger = logging.getLogger("curavia.tenancy")

#: Paths this middleware leaves alone.
#:
#: ``/api/`` is handled by the DRF base class. The rest must work before any
#: hospital is known: health checks, the schema, login, sign-up, the marketing
#: site and static files.
SKIP_PREFIXES = (
    "/api/",
    "/healthz",
    "/readyz",
    "/static/",
    "/accounts/",
    "/.well-known/",
)


class TenantScopeMiddleware:
    """
    Resolve the hospital for a session-authenticated Django request.

    Attaches ``request.organisation``, ``request.membership``,
    ``request.tenant_permissions`` and ``request.available_modules``, mirroring
    what the API base class provides, so a template or an admin view can rely
    on the same four names.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.organisation = None  # type: ignore[attr-defined]
        request.membership = None  # type: ignore[attr-defined]
        request.tenant_permissions = frozenset()  # type: ignore[attr-defined]
        request.available_modules = frozenset()  # type: ignore[attr-defined]

        if request.path.startswith(SKIP_PREFIXES):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return self.get_response(request)

        try:
            resolved = resolve(
                user,
                requested_organisation=request.META.get(
                    str(settings.CURAVIA["ORGANISATION_HEADER"])
                ),
                requested_branch=request.META.get(str(settings.CURAVIA["BRANCH_HEADER"])),
            )
        except ScopeDenied as exc:
            return JsonResponse({"detail": str(exc), "code": "scope_denied"}, status=403)

        if resolved is None:
            return self.get_response(request)

        request.organisation = resolved.organisation  # type: ignore[attr-defined]
        request.membership = resolved.membership  # type: ignore[attr-defined]
        request.tenant_permissions = resolved.permission_codes  # type: ignore[attr-defined]
        request.available_modules = resolved.available_modules  # type: ignore[attr-defined]

        token = set_scope(
            TenantScope(
                organisation_id=resolved.organisation.pk,
                branch_id=resolved.branch_id,
                all_branches=resolved.all_branches,
            )
        )
        try:
            apply_database_scope(resolved.organisation.pk)
            return self.get_response(request)
        finally:
            reset_scope(token)


def apply_database_scope(organisation_id: uuid.UUID) -> None:
    """
    Tell PostgreSQL which hospital this transaction belongs to, so row-level
    security policies can enforce the same boundary the ORM does.

    ``SET LOCAL`` semantics are why ``ATOMIC_REQUESTS`` is on: the setting is
    transaction-scoped, so it is guaranteed to exist here and guaranteed to be
    discarded when the request ends, rather than leaking to the next request
    that reuses a pooled connection.

    Off by default until the dedicated non-owner database role exists — a
    table's owner bypasses RLS unless the table is FORCEd, and Django's
    migration role is that owner. See docs/adr/0002-row-level-security.md.
    """
    if not settings.CURAVIA.get("ENABLE_RLS"):
        return
    with connection.cursor() as cursor:
        # set_config is a function, so the value is bound rather than
        # interpolated into SQL.
        cursor.execute("SELECT set_config('app.current_org', %s, true)", [str(organisation_id)])


class RequestContextMiddleware:
    """
    Carry the caller's IP, user agent and session key to wherever the audit log
    is written, without threading a ``request`` object through every service
    function — which would also not work from a Celery task or a WebSocket
    consumer.

    Also refreshes the device list, which is what makes "sign out everywhere"
    and immediate deactivation true rather than aspirational.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from . import request_context

        token = request_context.set(
            request_context.RequestInfo(
                ip=client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
                session_key=getattr(request.session, "session_key", "") or "",
            )
        )
        try:
            response = self.get_response(request)
        finally:
            request_context.reset(token)

        self._touch_session(request)
        return response

    @staticmethod
    def _touch_session(request: HttpRequest) -> None:
        user = getattr(request, "user", None)
        session_key = getattr(getattr(request, "session", None), "session_key", None)
        if not (user and user.is_authenticated and session_key):
            return
        from apps.accounts.models import UserSession

        UserSession.objects.update_or_create(
            session_key=session_key,
            defaults={
                "user": user,
                "ip": client_ip(request),
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
                "last_seen_at": timezone.now(),
            },
        )


def client_ip(request: HttpRequest) -> str | None:
    """
    The caller's address.

    The left-most entry of ``X-Forwarded-For`` is the original client; the rest
    are proxies. Behind Render this header is set by the platform.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def validate_module_settings() -> None:
    """Sanity check run from CoreConfig.ready, so a typo fails at boot."""
    unknown = set(settings.CURAVIA) - {
        "ENABLE_RLS",
        "BRANCH_HEADER",
        "ORGANISATION_HEADER",
        "EMERGENCY_ACCESS_MINUTES",
        "SIGNED_URL_TTL",
        "PERMISSIONS_MATRIX",
    }
    if unknown:
        raise RuntimeError(f"Unknown keys in settings.CURAVIA: {sorted(unknown)}")
    if not ALL_MODULE_CODES:
        raise RuntimeError("The module registry is empty.")
