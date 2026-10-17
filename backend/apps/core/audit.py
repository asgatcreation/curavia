"""
The audit log.

Two things make this more than a table of rows:

1. **Reads are logged, not just writes.** This is the part people skip, because
   reads are the awkward case — and it is the part that matters, since the
   real-world abuse is a staff member opening a record they have no business
   opening. Logging happens at the service layer rather than through model
   signals, because signals cannot see a read, cannot see the caller's reason,
   and fire during migrations.

2. **The chain is hashed.** Each row carries the previous row's hash, so a
   deleted or edited row breaks the chain and ``verify_audit_chain`` reports
   exactly where. A nightly ``AuditAnchor`` records the chain head, so
   tampering with the most recent rows is detectable too.

The table is insert-only at the database level as well: ``harden_database``
revokes UPDATE and DELETE on it from the application role.

Deliberately NOT a ``TenantScoped`` model: platform-level events have no
organisation. Reads are instead always filtered explicitly, and
``tests/isolation`` asserts that one hospital cannot reach another's log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .tenancy import current_organisation_id
from .utils import uuid7

logger = logging.getLogger("curavia.audit")

GENESIS_HASH = "0" * 64
PLATFORM_CHAIN = "platform"


class AuditFlag(models.TextChoices):
    """Why a row deserves a second look."""

    CROSS_BRANCH = "cross_branch", _("Record read across branches")
    EMERGENCY_ACCESS = "emergency_access", _("Break-the-glass access")
    SUPPORT_GRANT = "support_grant", _("Platform support access")
    AFTER_HOURS = "after_hours", _("Outside the actor's shift")
    SENSITIVE = "sensitive", _("Sensitive field read")
    OVERRIDE = "override", _("A clinical warning was overridden")
    BULK = "bulk", _("Bulk export or report")


class AuditEvent(models.Model):
    """One thing that happened, and who it happened by."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    organisation = models.ForeignKey(
        "core.Organisation",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
        db_index=True,
    )
    #: Monotonic per chain. Together with ``prev_hash`` this is what makes a
    #: missing row detectable, not merely suspicious.
    sequence = models.BigIntegerField()

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor_role = models.CharField(max_length=80, blank=True)
    action = models.CharField(max_length=80, db_index=True)

    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.UUIDField(null=True, blank=True)
    #: Denormalised so "every access to THIS patient" is one indexed query.
    patient_id = models.UUIDField(null=True, blank=True, db_index=True)

    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)

    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    session_key = models.CharField(max_length=60, blank=True)

    flags = ArrayField(models.CharField(max_length=32), default=list, blank=True)

    at = models.DateTimeField(default=timezone.now, db_index=True)

    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)

    class Meta:
        verbose_name = _("audit event")
        ordering = ("-at", "-sequence")
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "sequence"], name="unique_audit_sequence_per_chain"
            )
        ]
        indexes = [
            models.Index(fields=["organisation", "-at"]),
            models.Index(fields=["organisation", "actor", "-at"]),
            models.Index(fields=["organisation", "action", "-at"]),
        ]

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M} {self.action} by {self.actor_id or 'system'}"

    # -- hashing ---------------------------------------------------------

    def canonical_payload(self) -> str:
        """
        The exact bytes that get hashed.

        Field order and formatting are fixed, so the same row always produces
        the same hash on any machine and any Python version.
        """
        return json.dumps(
            {
                "id": str(self.id),
                "organisation": str(self.organisation_id) if self.organisation_id else None,
                "sequence": self.sequence,
                "actor": str(self.actor_id) if self.actor_id else None,
                "actor_role": self.actor_role,
                "action": self.action,
                "object_type": self.object_type,
                "object_id": str(self.object_id) if self.object_id else None,
                "patient_id": str(self.patient_id) if self.patient_id else None,
                "changes": self.changes,
                "reason": self.reason,
                "ip": self.ip,
                "session_key": self.session_key,
                "flags": sorted(self.flags or []),
                "at": self.at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str,
        )

    def compute_hash(self) -> str:
        digest = hashlib.sha256()
        digest.update(self.prev_hash.encode("ascii"))
        digest.update(self.canonical_payload().encode("utf-8"))
        return digest.hexdigest()


class AuditChainHead(models.Model):
    """
    The tip of one chain, locked while a row is appended.

    Without this, two concurrent requests could read the same ``prev_hash`` and
    produce a fork that verification would report as tampering.
    """

    chain_key = models.CharField(max_length=80, primary_key=True)
    sequence = models.BigIntegerField(default=0)
    head_hash = models.CharField(max_length=64, default=GENESIS_HASH)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("audit chain head")

    def __str__(self) -> str:
        return f"{self.chain_key} @ {self.sequence}"


class AuditAnchor(models.Model):
    """
    A nightly notarisation of the chain tip.

    Makes tampering with recent rows detectable: an attacker who rewrites the
    last fifty rows and re-hashes them still cannot match yesterday's anchor.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    chain_key = models.CharField(max_length=80, db_index=True)
    sequence = models.BigIntegerField()
    head_hash = models.CharField(max_length=64)
    event_count = models.BigIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _("audit anchor")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"anchor {self.chain_key} @ {self.sequence}"


# --------------------------------------------------------------------------
# The service layer — always use this, never AuditEvent.objects.create()
# --------------------------------------------------------------------------


def chain_key_for(organisation_id: uuid.UUID | str | None) -> str:
    return f"org:{organisation_id}" if organisation_id else PLATFORM_CHAIN


def record(
    action: str,
    *,
    actor=None,
    actor_role: str = "",
    organisation_id: uuid.UUID | str | None = None,
    object_type: str = "",
    object_id: uuid.UUID | str | None = None,
    patient_id: uuid.UUID | str | None = None,
    changes: dict[str, Any] | None = None,
    reason: str = "",
    ip: str | None = None,
    user_agent: str = "",
    session_key: str = "",
    flags: list[str] | None = None,
) -> AuditEvent:
    """
    Append one event to the organisation's chain.

    Call this for every view and every change of a patient record. ``reason``
    is mandatory in practice for break-the-glass access, waivers and
    overridden clinical warnings — the callers that need it pass it.

    Runs in its own transaction block holding a row lock on the chain head, so
    the sequence and the hash chain stay consistent under concurrency.
    """
    if organisation_id is None:
        organisation_id = current_organisation_id()

    key = chain_key_for(organisation_id)

    with transaction.atomic():
        head, _created = AuditChainHead.objects.select_for_update().get_or_create(chain_key=key)
        event = AuditEvent(
            organisation_id=organisation_id,
            sequence=head.sequence + 1,
            actor=actor,
            actor_role=actor_role or "",
            action=action,
            object_type=object_type,
            object_id=object_id,
            patient_id=patient_id,
            changes=changes or {},
            reason=reason,
            ip=ip,
            user_agent=(user_agent or "")[:300],
            session_key=session_key or "",
            flags=sorted(set(flags or [])),
            at=timezone.now(),
            prev_hash=head.head_hash,
        )
        event.hash = event.compute_hash()
        event.save(force_insert=True)

        head.sequence = event.sequence
        head.head_hash = event.hash
        head.save(update_fields=["sequence", "head_hash", "updated_at"])

    logger.info(
        "audit %s action=%s actor=%s object=%s:%s flags=%s",
        key,
        action,
        event.actor_id,
        object_type,
        object_id,
        ",".join(event.flags),
    )
    return event


def record_view(
    *,
    actor,
    object_type: str,
    object_id: uuid.UUID | str,
    patient_id: uuid.UUID | str | None = None,
    fields: list[str] | None = None,
    **kwargs: Any,
) -> AuditEvent:
    """
    Log that somebody *looked* at something.

    ``fields`` names what was returned rather than its contents — an audit log
    full of patient data would be a second copy of the records to protect.
    """
    changes: dict[str, Any] = {"read": sorted(fields)} if fields else {}
    return record(
        f"{object_type.lower()}.view",
        actor=actor,
        object_type=object_type,
        object_id=object_id,
        patient_id=patient_id,
        changes=changes,
        **kwargs,
    )


class ChainVerificationError(Exception):
    """Raised by verify_chain when a chain does not hold."""

    def __init__(self, chain_key: str, sequence: int, problem: str) -> None:
        super().__init__(f"chain {chain_key} broken at sequence {sequence}: {problem}")
        self.chain_key = chain_key
        self.sequence = sequence
        self.problem = problem


def verify_chain(organisation_id: uuid.UUID | str | None) -> tuple[int, str]:
    """
    Walk one chain from the beginning and recompute every hash.

    Returns ``(event_count, head_hash)`` if the chain holds, and raises
    ``ChainVerificationError`` naming the exact sequence number if it does not.
    """
    key = chain_key_for(organisation_id)
    previous = GENESIS_HASH
    count = 0

    # organisation_id is None for the platform chain, which is a real lookup
    # (organisation IS NULL) that django-stubs does not model.
    queryset = (
        AuditEvent.objects.filter(organisation_id=organisation_id)  # type: ignore[misc]
        .order_by("sequence")
        .iterator(chunk_size=500)
    )
    for expected_sequence, event in enumerate(queryset, start=1):
        if event.sequence != expected_sequence:
            raise ChainVerificationError(
                key, event.sequence, f"expected sequence {expected_sequence} — a row is missing"
            )
        if event.prev_hash != previous:
            raise ChainVerificationError(
                key, event.sequence, "prev_hash does not match the previous row's hash"
            )
        recomputed = event.compute_hash()
        if recomputed != event.hash:
            raise ChainVerificationError(
                key, event.sequence, "row contents do not match its stored hash"
            )
        previous = event.hash
        count += 1

    head = AuditChainHead.objects.filter(chain_key=key).first()
    if head and (head.sequence != count or head.head_hash != previous):
        raise ChainVerificationError(
            key, count, "the chain head does not match the last row — rows were removed"
        )
    return count, previous
