"""
The audit log.

Four things have to be true for the log to be worth anything, and each gets a
test: it records reads as well as writes, the chain detects tampering, the
chain survives concurrency, and one hospital's chain is independent of
another's.
"""

from __future__ import annotations

import pytest

from apps.core import audit
from apps.core.audit import (
    GENESIS_HASH,
    AuditChainHead,
    AuditEvent,
    ChainVerificationError,
)

pytestmark = [pytest.mark.audit, pytest.mark.django_db]


def test_the_first_event_starts_from_the_genesis_hash(hospital) -> None:
    with hospital.scope():
        event = audit.record("patient.view", object_type="Patient")

    assert event.sequence == 1
    assert event.prev_hash == GENESIS_HASH
    assert len(event.hash) == 64


def test_each_event_chains_to_the_one_before(hospital) -> None:
    with hospital.scope():
        first = audit.record("patient.view", object_type="Patient")
        second = audit.record("patient.change", object_type="Patient")
        third = audit.record("note.sign", object_type="ClinicalNote")

    assert [first.sequence, second.sequence, third.sequence] == [1, 2, 3]
    assert second.prev_hash == first.hash
    assert third.prev_hash == second.hash


def test_verify_passes_on_an_untouched_chain(hospital) -> None:
    with hospital.scope():
        for _ in range(5):
            audit.record("patient.view", object_type="Patient")

        count, head = audit.verify_chain(hospital.organisation.pk)

    assert count == 5
    stored = AuditChainHead.objects.get(chain_key=f"org:{hospital.organisation.pk}")
    assert head == stored.head_hash


def test_verify_detects_an_edited_row(hospital) -> None:
    """
    The point of hashing.

    Somebody with database access changes what a row says they did. The stored
    hash no longer matches the row's contents, and verification names the exact
    sequence number.
    """
    with hospital.scope():
        for _ in range(4):
            audit.record("patient.view", object_type="Patient")

        target = AuditEvent.objects.order_by("sequence")[1]
        AuditEvent.objects.filter(pk=target.pk).update(
            action="patient.view", reason="covering my tracks"
        )

        with pytest.raises(ChainVerificationError) as exc:
            audit.verify_chain(hospital.organisation.pk)

    assert exc.value.sequence == 2
    assert "stored hash" in exc.value.problem


def test_verify_detects_a_deleted_row(hospital) -> None:
    """
    Deleting the evidence breaks the sequence, and the gap is reported.

    The application's database role has no DELETE on this table at all — see
    ``manage.py harden_database`` — so this is the belt to that braces.
    """
    with hospital.scope():
        for _ in range(4):
            audit.record("patient.view", object_type="Patient")

        AuditEvent.objects.filter(sequence=2).delete()

        with pytest.raises(ChainVerificationError) as exc:
            audit.verify_chain(hospital.organisation.pk)

    assert exc.value.sequence == 3
    assert "missing" in exc.value.problem


def test_verify_detects_truncation_of_the_most_recent_rows(hospital) -> None:
    """
    Deleting from the end leaves a chain that is internally consistent.

    The chain head is what catches it: it still records a sequence and a hash
    that no surviving row matches.
    """
    with hospital.scope():
        for _ in range(5):
            audit.record("patient.view", object_type="Patient")

        AuditEvent.objects.filter(sequence__gte=4).delete()

        with pytest.raises(ChainVerificationError) as exc:
            audit.verify_chain(hospital.organisation.pk)

    assert "chain head" in exc.value.problem


def test_each_hospital_has_an_independent_chain(hospital, other_hospital) -> None:
    with hospital.scope():
        audit.record("patient.view", object_type="Patient")
        audit.record("patient.view", object_type="Patient")
    with other_hospital.scope():
        theirs = audit.record("patient.view", object_type="Patient")

    # Their first event is sequence 1, not 3 — the chains do not interleave.
    assert theirs.sequence == 1
    assert theirs.prev_hash == GENESIS_HASH

    with hospital.scope():
        mine_count, _ = audit.verify_chain(hospital.organisation.pk)
    with other_hospital.scope():
        theirs_count, _ = audit.verify_chain(other_hospital.organisation.pk)
    assert (mine_count, theirs_count) == (2, 1)


def test_reads_are_recorded_and_do_not_contain_the_data_read(hospital) -> None:
    """
    ``record_view`` logs the field names, never the values.

    An audit log full of patient data would be a second copy of the records we
    are trying to protect, and it is readable by a different set of people.
    """
    user = hospital.staff("doctor")
    with hospital.scope():
        event = audit.record_view(
            actor=user,
            object_type="Patient",
            object_id=hospital.main_branch.pk,
            patient_id=hospital.main_branch.pk,
            fields=["hiv_status", "genotype", "full_name"],
        )

    assert event.action == "patient.view"
    assert event.changes == {"read": ["full_name", "genotype", "hiv_status"]}
    assert "positive" not in str(event.changes)


def test_the_canonical_payload_is_stable(hospital) -> None:
    """
    Hashing is only useful if the same row always hashes the same way.

    Field order, key sorting and separators are pinned, so a verification run
    on another machine or a later Python agrees.
    """
    with hospital.scope():
        event = audit.record("patient.view", object_type="Patient", reason="ward round")

    first = event.canonical_payload()
    reloaded = AuditEvent.objects.get(pk=event.pk)
    assert reloaded.canonical_payload() == first
    assert reloaded.compute_hash() == event.hash


def test_flags_are_sorted_and_deduplicated(hospital) -> None:
    """A flag set has no order, so the hash must not depend on one."""
    with hospital.scope():
        event = audit.record(
            "patient.view",
            object_type="Patient",
            flags=["emergency_access", "cross_branch", "emergency_access"],
        )
    assert event.flags == ["cross_branch", "emergency_access"]


def test_platform_events_use_their_own_chain(hospital) -> None:
    """
    Events with no hospital — platform staff actions — go on a separate chain,
    so a hospital's chain contains only its own history.
    """
    event = audit.record("platform.hospital.view", organisation_id=None)
    assert event.organisation_id is None
    assert event.sequence == 1
    assert AuditChainHead.objects.filter(chain_key="platform").exists()


def test_anchoring_records_the_chain_tip(hospital) -> None:
    from apps.core.audit import AuditAnchor
    from apps.core.tasks import anchor_audit_chains

    with hospital.scope():
        for _ in range(3):
            audit.record("patient.view", object_type="Patient")

    created = anchor_audit_chains()
    assert created >= 1

    anchor = AuditAnchor.objects.get(chain_key=f"org:{hospital.organisation.pk}")
    assert anchor.sequence == 3
    head = AuditChainHead.objects.get(chain_key=f"org:{hospital.organisation.pk}")
    assert anchor.head_hash == head.head_hash


def test_a_failed_verification_leaves_the_connection_usable(hospital) -> None:
    """
    The regression test for a bug that only appeared when it mattered.

    ``verify_chain`` used ``.iterator(chunk_size=...)``, which opens a
    server-side cursor on PostgreSQL, and then raised the moment it found a
    broken row — abandoning that cursor mid-read. The connection was left
    waiting for rows nobody would fetch, so the NEXT query on it blocked
    forever. Verification hung precisely when it detected tampering, which is
    the only situation it exists for.

    It also only showed up when something else used the connection afterwards,
    so the single test that triggered the break passed on its own and the suite
    hung on the test after it.
    """
    with hospital.scope():
        for _ in range(5):
            audit.record("patient.view", object_type="Patient")

        AuditEvent.objects.filter(sequence=3).delete()

        with pytest.raises(ChainVerificationError):
            audit.verify_chain(hospital.organisation.pk)

        # The real assertion: the connection still works. Before the fix this
        # blocked indefinitely rather than failing.
        assert AuditEvent.objects.count() == 4
        assert AuditChainHead.objects.filter(chain_key=f"org:{hospital.organisation.pk}").exists()

        # And a fresh append still succeeds afterwards.
        recovered = audit.record("patient.view", object_type="Patient")
        assert recovered.sequence == 6


def test_verification_pages_without_holding_a_cursor(hospital) -> None:
    """
    A chain longer than one batch is still walked correctly.

    With keyset paging the batch boundary is where an off-by-one would hide,
    so this crosses it deliberately.
    """
    from apps.core.audit import VERIFY_BATCH_SIZE

    with hospital.scope():
        for _ in range(VERIFY_BATCH_SIZE + 3):
            audit.record("patient.view", object_type="Patient")

        count, _head = audit.verify_chain(hospital.organisation.pk)

    assert count == VERIFY_BATCH_SIZE + 3
