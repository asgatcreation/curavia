"""
Where the tenant scope comes from.

One rule governs this whole file: **the client never tells us which hospital it
is.** The scope is derived from the authenticated user's membership. A forged
header changes nothing, because every header is validated against what that
user actually belongs to.

A client may ask for a particular organisation or branch — a visiting
consultant works at three hospitals and a group officer covers five branches —
but asking for one they have no membership in is a 403, not a silent switch.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone

from .modules import ALL_MODULE_CODES
from .tenancy import TenantScope, reset_scope, set_scope

logger = logging.getLogger("curavia.tenancy")

#: Paths that must work before any tenant is known: health checks, the schema,
#: login, sign-up, the marketing site and the admin.
UNSCOPED_PREFIXES = (
    "/healthz",
    "/readyz",
    "/api/v1/auth/",
    "/api/v1/schema",
    "/api/docs",
    "/admin/",
    "/static/",
    "/accounts/",
    "/.well-known/",
)


class TenantScopeMiddleware:
    """
    Resolve the hospital and branch for this request, and set them for the
    duration of it.

    Also attaches, for the permission classes and serializers to use:

    * ``request.organisation``        — the Organisation instance
    * ``request.membership``         — the user's StaffMembership
    * ``request.tenant_permissions`` — the codes they hold here
    * ``request.available_modules``  — modules in the plan and switched on
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.organisation = None  # type: ignore[attr-defined]
        request.membership = None  # type: ignore[attr-defined]
        request.tenant_permissions = frozenset()  # type: ignore[attr-defined]
        request.available_modules = frozenset()  # type: ignore[attr-defined]

        if self._is_unscoped_path(request.path) or not self._is_authenticated(request):
            return self.get_response(request)

        user = request.user
        assert not isinstance(user, AnonymousUser)  # _is_authenticated checked this
        try:
            scope_data = self._resolve(request, user)
        except PermissionError as exc:
            return JsonResponse({"detail": str(exc)}, status=403)

        if scope_data is None:
            # Authenticated but belongs to no hospital — a patient-portal user,
            # or a platform staff member who has not been granted access.
            return self.get_response(request)

        membership, organisation, branch_id, all_branches = scope_data

        request.organisation = organisation  # type: ignore[attr-defined]
        request.membership = membership  # type: ignore[attr-defined]
        request.tenant_permissions = user.permission_codes(organisation.pk)  # type: ignore[attr-defined]
        request.available_modules = organisation.available_modules()  # type: ignore[attr-defined]

        token = set_scope(
            TenantScope(
                organisation_id=organisation.pk,
                branch_id=branch_id,
                all_branches=all_branches,
            )
        )
        try:
            self._apply_database_scope(organisation.pk)
            return self.get_response(request)
        finally:
            reset_scope(token)

    # -- internals -------------------------------------------------------

    @staticmethod
    def _is_unscoped_path(path: str) -> bool:
        return path.startswith(UNSCOPED_PREFIXES)

    @staticmethod
    def _is_authenticated(request: HttpRequest) -> bool:
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated)

    def _resolve(self, request: HttpRequest, user):  # type: ignore[no-untyped-def]
        requested_org = self._header(request, str(settings.CURAVIA["ORGANISATION_HEADER"]))
        requested_branch = self._header(request, str(settings.CURAVIA["BRANCH_HEADER"]))

        memberships = list(user.live_memberships())
        if not memberships:
            return None

        if requested_org:
            membership = next(
                (m for m in memberships if str(m.organisation_id) == requested_org), None
            )
            if membership is None:
                # The user asked for a hospital they do not work at. Say no, and
                # record it: a client doing this is either broken or probing.
                logger.warning(
                    "rejected organisation header %s for user %s", requested_org, user.pk
                )
                raise PermissionError("You do not have access to that hospital.")
        else:
            membership = self._preferred(memberships, user.last_organisation_id)

        organisation = membership.organisation
        permission_codes = user.permission_codes(organisation.pk)
        all_branches = "branch.view_all" in permission_codes

        branch_id = self._resolve_branch(
            membership, requested_branch, user.last_branch_id, all_branches
        )
        return membership, organisation, branch_id, all_branches

    @staticmethod
    def _preferred(memberships, last_organisation_id):  # type: ignore[no-untyped-def]
        if last_organisation_id:
            for membership in memberships:
                if membership.organisation_id == last_organisation_id:
                    return membership
        return memberships[0]

    @staticmethod
    def _resolve_branch(membership, requested, last_branch_id, all_branches):  # type: ignore[no-untyped-def]
        allowed = list(membership.branches.all())
        allowed_ids = {str(branch.pk) for branch in allowed}

        if requested:
            if requested not in allowed_ids and not all_branches:
                raise PermissionError("You do not have access to that branch.")
            return uuid.UUID(requested)

        if last_branch_id and str(last_branch_id) in allowed_ids:
            return last_branch_id
        if membership.default_branch_id:
            return membership.default_branch_id
        return allowed[0].pk if allowed else None

    @staticmethod
    def _header(request: HttpRequest, key: str) -> str | None:
        value = request.META.get(key)
        if not value:
            return None
        try:
            return str(uuid.UUID(value.strip()))
        except (ValueError, AttributeError) as exc:
            raise PermissionError("That organisation or branch identifier is not valid.") from exc

    @staticmethod
    def _apply_database_scope(organisation_id: uuid.UUID) -> None:
        """
        Tell PostgreSQL which hospital this transaction belongs to, so
        row-level security policies can enforce the same boundary the ORM does.

        ``SET LOCAL`` is transaction-scoped, which is why ``ATOMIC_REQUESTS``
        is on: it guarantees we are inside a transaction here, and that the
        setting is discarded when the request ends rather than leaking to the
        next request on a pooled connection.

        Off by default until the dedicated non-owner database role exists —
        a table's owner bypasses RLS unless the table is FORCEd, and Django's
        migration role is that owner. See docs/adr/0002-row-level-security.md.
        """
        if not settings.CURAVIA.get("ENABLE_RLS"):
            return
        with connection.cursor() as cursor:
            # Parameterised: set_config is a function, so the value is bound
            # rather than interpolated into SQL.
            cursor.execute("SELECT set_config('app.current_org', %s, true)", [str(organisation_id)])


class RequestContextMiddleware:
    """
    Carry the caller's IP, user agent and session key to wherever the audit log
    is written, without threading a ``request`` object through every service
    function.

    Also refreshes the device list, which is what makes the "sign out
    everywhere" button and immediate deactivation work.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from . import request_context

        token = request_context.set(
            request_context.RequestInfo(
                ip=self._client_ip(request),
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
    def _client_ip(request: HttpRequest) -> str | None:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            # Left-most entry is the original client; the rest are proxies.
            return forwarded.split(",")[0].strip() or None
        return request.META.get("REMOTE_ADDR") or None

    @staticmethod
    def _touch_session(request: HttpRequest) -> None:
        user = getattr(request, "user", None)
        session_key = getattr(request.session, "session_key", None)
        if not (user and user.is_authenticated and session_key):
            return
        from apps.accounts.models import UserSession

        UserSession.objects.update_or_create(
            session_key=session_key,
            defaults={
                "user": user,
                "ip": RequestContextMiddleware._client_ip(request),
                "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
                "last_seen_at": timezone.now(),
            },
        )


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
    assert ALL_MODULE_CODES, "The module registry is empty."
