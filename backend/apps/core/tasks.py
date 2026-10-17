"""
Background work, behind one function.

Everything asynchronous in Curavia goes through ``enqueue``. That is not
ceremony: the hosting decision (docs/00-decisions.md §3) is Render's free tier,
which has no background worker, so Celery runs inside the web process and may
have to be dropped for Django's built-in database-backed tasks if memory gets
tight. Keeping one call site means that is a change to this file, not to
thirty.

Two rules every task obeys:

* **A task carries its ``organisation_id``.** A task body runs with no request,
  so the tenant scope has to be re-established explicitly. ``TenantTask`` does
  it, and a task that forgets the argument fails loudly rather than running
  unscoped.
* **Tasks are idempotent.** They are retried, and a retried "send the
  appointment reminder" must not text the patient twice.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from celery import Task, shared_task
from django.conf import settings

from .tenancy import tenant_scope

logger = logging.getLogger(__name__)


class TenantTask(Task):
    """
    Base class for any task that touches hospital data.

    Re-establishes the tenant scope from the ``organisation_id`` keyword, which
    is mandatory. Without it the task would run with no scope and the managers
    in ``apps.core.tenancy`` would raise — loudly, which is the point.
    """

    abstract = True

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        organisation_id = kwargs.get("organisation_id")
        if organisation_id is None:
            raise ValueError(
                f"{self.name} is a TenantTask and must be called with "
                f"organisation_id=... so it knows which hospital it is acting for."
            )
        branch_id = kwargs.get("branch_id")
        with tenant_scope(organisation_id, branch_id):
            return super().__call__(*args, **kwargs)


def tenant_task(*args: Any, **kwargs: Any) -> Callable[..., Any]:
    """``@tenant_task`` — a shared_task that restores the tenant scope."""
    kwargs.setdefault("base", TenantTask)
    kwargs.setdefault("bind", False)
    return shared_task(*args, **kwargs)


def enqueue(
    task: Any,
    *args: Any,
    organisation_id: uuid.UUID | str | None = None,
    countdown: int | None = None,
    **kwargs: Any,
) -> Any:
    """
    Run something in the background.

    The single seam between Curavia and whatever queue is behind it. Pass the
    task object, not its name, so a typo is an ImportError at startup rather
    than a job that silently never runs.
    """
    if organisation_id is not None:
        kwargs["organisation_id"] = str(organisation_id)

    backend = settings.BACKGROUND_TASK_BACKEND
    if backend == "celery":
        return task.apply_async(args=args, kwargs=kwargs, countdown=countdown)
    if backend == "eager":
        # Used by the test settings and by a single-process deployment that has
        # no worker at all.
        return task(*args, **kwargs)
    raise RuntimeError(f"Unknown BACKGROUND_TASK_BACKEND: {backend!r}")


# --------------------------------------------------------------------------
# Foundation tasks
# --------------------------------------------------------------------------


@shared_task(name="core.anchor_audit_chains")
def anchor_audit_chains() -> int:
    """
    Notarise every audit chain's current tip.

    Runs nightly. An attacker who rewrites the last fifty audit rows and
    re-hashes them still cannot match last night's anchor, so tampering with
    recent history stays detectable.
    """
    from .audit import AuditAnchor, AuditChainHead

    created = 0
    for head in AuditChainHead.objects.all().iterator():
        AuditAnchor.objects.create(
            chain_key=head.chain_key,
            sequence=head.sequence,
            head_hash=head.head_hash,
            event_count=head.sequence,
        )
        created += 1
    logger.info("anchored %s audit chain(s)", created)
    return created


@shared_task(name="core.expire_emergency_grants")
def expire_emergency_grants() -> int:
    """
    Tidy up break-the-glass grants that have run out.

    The permission predicate already checks ``expires_at``, so this is
    housekeeping rather than enforcement — expiry is never dependent on a job
    having run.
    """
    from django.utils import timezone

    from .models import EmergencyAccessGrant
    from .tenancy import unscoped

    with unscoped(reason="housekeeping: expire break-the-glass grants platform-wide"):
        stale = EmergencyAccessGrant.unscoped.filter(
            revoked_at__isnull=True, expires_at__lte=timezone.now()
        )
        count = stale.update(revoked_at=timezone.now())
    logger.info("closed %s expired emergency access grant(s)", count)
    return count
