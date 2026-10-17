"""
Tenant scoping.

Curavia keeps every hospital in one database, one schema, with an
``organisation_id`` on every row. That is what real multi-tenant SaaS does,
but it has one failure mode: a developer forgets a filter and Hospital A sees
Hospital B's patients.

So the ability to forget is removed. ``TenantManager`` raises
``TenantScopeMissing`` rather than returning unfiltered rows, which turns a
silent data leak into a loud crash in development and a 500 in production —
the right way round. The scope itself is set once, at the edge, from the
authenticated user's membership; the client never supplies it.

Four layers in total, of which this module is the first two:

1. this manager, which cannot be queried unscoped
2. the ``contextvar`` below, set by middleware (HTTP), the Channels
   middleware (WebSocket) and ``TenantTask`` (Celery)
3. PostgreSQL row-level security — ``apps.core.rls``
4. the attack suite in ``backend/tests/isolation``

``contextvars`` rather than thread-locals: Channels consumers and async views
run many requests in one thread, and a thread-local would leak one hospital's
scope into another's request.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from django.db import models

logger = logging.getLogger("curavia.tenancy")

__all__ = [
    "TenantManager",
    "TenantQuerySet",
    "TenantScope",
    "TenantScopeMissing",
    "UnscopedManager",
    "current_branch_id",
    "current_organisation_id",
    "current_scope",
    "tenant_scope",
    "unscoped",
]


class TenantScopeMissing(RuntimeError):
    """
    Raised when a tenant-scoped model is queried with no active scope.

    This is a programming error, never a user error. If you see it, either the
    code path needs ``with tenant_scope(org_id):`` or it is a genuinely
    cross-tenant job that should use ``Model.unscoped`` inside
    ``with unscoped(reason=...)``.
    """


@dataclass(frozen=True, slots=True)
class TenantScope:
    organisation_id: uuid.UUID
    branch_id: uuid.UUID | None = None
    #: True when the caller may read every branch in the organisation.
    all_branches: bool = False


_scope: ContextVar[TenantScope | None] = ContextVar("curavia_tenant_scope", default=None)
_unscoped: ContextVar[bool] = ContextVar("curavia_unscoped", default=False)


def current_scope() -> TenantScope | None:
    return _scope.get()


def current_organisation_id() -> uuid.UUID | None:
    scope = _scope.get()
    return scope.organisation_id if scope else None


def current_branch_id() -> uuid.UUID | None:
    scope = _scope.get()
    return scope.branch_id if scope else None


def set_scope(scope: TenantScope | None) -> Token[TenantScope | None]:
    """Set the scope and return the token needed to restore the previous one."""
    return _scope.set(scope)


def reset_scope(token: Token[TenantScope | None]) -> None:
    _scope.reset(token)


@contextmanager
def tenant_scope(
    organisation_id: uuid.UUID | str,
    branch_id: uuid.UUID | str | None = None,
    *,
    all_branches: bool = False,
) -> Iterator[TenantScope]:
    """
    Run a block scoped to one hospital.

    Used by the middleware, by Celery tasks, by the demo seeder and by tests.
    Nested calls are fine; the previous scope is restored on exit even if the
    block raises.
    """
    scope = TenantScope(
        organisation_id=_as_uuid(organisation_id),
        branch_id=_as_uuid(branch_id) if branch_id else None,
        all_branches=all_branches,
    )
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


@contextmanager
def unscoped(*, reason: str) -> Iterator[None]:
    """
    Deliberately query across every hospital.

    There are only a few legitimate reasons: the platform console's aggregate
    counts, the nightly demo reset, data migrations and the test suite's own
    setup. ``reason`` is required so the intent is readable at the call site,
    and it is logged at WARNING so these appear in production logs.

    CI greps for uses of this helper and of ``Model.unscoped`` and fails if one
    appears outside ``docs/cross-tenant-allowlist.txt`` — so the dangerous path
    stays visible instead of spreading quietly.
    """
    logger.warning("cross-tenant query: %s", reason)
    token = _unscoped.set(True)
    try:
        yield
    finally:
        _unscoped.reset(token)


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class TenantQuerySet(models.QuerySet):
    """A queryset that knows which hospital it belongs to."""

    def for_branch(self, branch_id: uuid.UUID | str) -> TenantQuerySet:
        return self.filter(branch_id=_as_uuid(branch_id))

    def in_current_branch(self) -> TenantQuerySet:
        """
        Narrow to the active branch, unless the caller may see all branches.

        Only meaningful on ``BranchScoped`` models; on others it is a no-op so
        that generic view code can call it unconditionally.
        """
        if not any(f.name == "branch" for f in self.model._meta.fields):
            return self
        scope = _scope.get()
        if scope is None or scope.all_branches or scope.branch_id is None:
            return self
        return self.filter(branch_id=scope.branch_id)

    def live(self) -> TenantQuerySet:
        """Exclude soft-deleted rows. Curavia never issues a hard DELETE."""
        return self.filter(archived_at__isnull=True)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):  # type: ignore[misc]
    """
    The default manager on every tenant-scoped model.

    Refuses to build a queryset when no scope is active.
    """

    def none(self) -> TenantQuerySet:
        """
        An empty queryset, with no scope required.

        Safe by construction: ``none()`` can never return a row, whatever the
        scope. The exception exists because introspection tools — drf-spectacular
        and django-filter building a filterset — call ``Model._default_manager.none()``
        with no request, and failing there would mean either no API schema or a
        weaker guard everywhere.
        """
        # Built directly rather than through super().none(), which would route
        # back through the scoped get_queryset() and raise.
        return TenantQuerySet(self.model, using=self._db).none()

    def get_queryset(self) -> TenantQuerySet:
        qs: TenantQuerySet = super().get_queryset()
        if _unscoped.get():
            return qs
        organisation_id = current_organisation_id()
        if organisation_id is None:
            raise TenantScopeMissing(
                f"{self.model.__name__} was queried with no tenant scope active. "
                f"Wrap the call in `with tenant_scope(organisation_id):`, or use "
                f"`{self.model.__name__}.unscoped` inside `with unscoped(reason=...)` "
                f"if this really is a cross-hospital query."
            )
        return qs.filter(organisation_id=organisation_id)


class UnscopedManager(models.Manager.from_queryset(TenantQuerySet)):  # type: ignore[misc]
    """
    Every tenant model's escape hatch, and its ``base_manager_name``.

    Django uses the base manager internally to follow a forward foreign key
    (``encounter.organisation``). If that used the scoped manager, ordinary
    attribute access would explode inside migrations and the admin. Reverse
    relations (``patient.encounters``) still go through the scoped default
    manager, which is where the protection actually matters.
    """
