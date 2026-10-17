"""
The request contract.

``TenantScopeMiddleware`` attaches four things to every authenticated request:
the hospital, the membership, the permission codes held there, and the modules
available. Views and serializers rely on all four, but DRF's own ``Request``
knows nothing about them, so every use would otherwise be an untyped
``getattr`` — and a renamed attribute would be found by a 500 in production
rather than by the type checker.

``CuraviaRequest`` states the contract instead. Annotating a view's ``request``
with it documents what the middleware guarantees and makes a typo a build
failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from rest_framework.request import Request

if TYPE_CHECKING:
    from apps.accounts.models import StaffMembership, User
    from apps.core.models import Organisation


class CuraviaRequest(Request):
    """
    A ``Request`` after ``TenantScopeMiddleware`` has run.

    Not instantiated anywhere — it exists so a view can say what it needs.
    ``user`` is narrowed to ``User`` because every view annotated with this is
    behind ``IsAuthenticated``; an anonymous caller never reaches the body.
    """

    user: User
    organisation: Organisation | None
    membership: StaffMembership | None
    tenant_permissions: frozenset[str]
    available_modules: frozenset[str]


class AuditContext(TypedDict):
    """
    What ``apps.core.request_context.as_audit_kwargs`` returns.

    A ``TypedDict`` rather than a plain dict so that ``**as_audit_kwargs()``
    maps onto ``audit.record``'s keyword arguments and is checked, instead of
    collapsing to ``dict[str, str | None]`` and silently matching anything.
    """

    ip: str | None
    user_agent: str
    session_key: str
