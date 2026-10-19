"""
The permissions matrix test.

Generated from ``docs/permissions-matrix.yml``: one test case per
(permission x role), which at milestone 2 is around a thousand assertions from
a file a human can actually read and argue with.

Three layers are checked:

1. **The role template** grants exactly the codes the matrix says it should.
   This is the layer that catches somebody widening a cashier's access.
2. **The rule engine** — ``user.has_perm(code, obj)`` — agrees, which checks
   that the object-level predicates in ``apps/core/rules.py`` compose the way
   they are meant to.
3. **The real API**, where the matrix declares an endpoint: a 200 for a role
   that should be allowed and a 403 for one that should not. A rule that is
   right in the engine and not wired into a view protects nothing.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.matrix import load_matrix
from apps.core.permissions import ALL_PERMISSION_CODES
from tests.factories import build_hospital, role_codes

pytestmark = [pytest.mark.permissions, pytest.mark.django_db]

MATRIX = load_matrix()


def _ids(argvalues) -> list[str]:
    return [f"{entry.permission}::{role}" for entry, role, _ in argvalues]


# --------------------------------------------------------------------------
# Layer 0 — the matrix itself has to be sane
# --------------------------------------------------------------------------


def test_matrix_only_mentions_real_permissions() -> None:
    """A code in the matrix that does not exist means the matrix is stale."""
    unknown = MATRIX.covered_permissions() - ALL_PERMISSION_CODES
    assert not unknown, f"The matrix mentions permissions that do not exist: {sorted(unknown)}"


def test_matrix_only_mentions_real_roles() -> None:
    from apps.accounts.role_templates import ROLE_TEMPLATES_BY_SLUG

    unknown = set(MATRIX.roles) - set(ROLE_TEMPLATES_BY_SLUG)
    assert not unknown, f"The matrix lists roles with no template: {sorted(unknown)}"


def test_every_role_template_appears_in_the_matrix() -> None:
    """
    A role the matrix has never heard of would be tested against nothing.

    This is the check that forces a decision about every permission when a new
    role is added, rather than letting it inherit silence.
    """
    from apps.accounts.role_templates import ROLE_TEMPLATES_BY_SLUG

    missing = set(ROLE_TEMPLATES_BY_SLUG) - set(MATRIX.roles)
    assert not missing, (
        f"These role templates are not in docs/permissions-matrix.yml, so nothing "
        f"checks what they can do: {sorted(missing)}"
    )


def test_owner_is_never_locked_out_of_its_own_hospital() -> None:
    """
    The owner must keep the permissions that make a hospital recoverable:
    settings, staff, roles and the audit log. A matrix that took one of these
    away would make a hospital unadministrable.
    """
    essential = {"org.settings.change", "staff.manage", "role.manage", "audit.view"}
    for code in essential:
        entry = MATRIX.by_permission(code)
        assert entry is not None, f"{code} must be in the matrix"
        assert "owner" in entry.allow, f"The owner must always hold {code}"


def test_separation_of_duties_holds() -> None:
    """
    Pairs that must never land on the same role.

    These are the controls an auditor actually looks for, so they are asserted
    directly rather than left implicit in the big table.
    """
    # Lab verification is deliberately NOT here, and the first version of this
    # test was wrong to include it.
    #
    # "A scientist must not verify their own result" is a control on the
    # RESULT, not on the role. In a small Nigerian laboratory the supervisor is
    # the second pair of eyes and also works the bench, so a role holding both
    # codes is normal and correct. What must never happen is one person
    # entering a result and then verifying that same result — an object-level
    # rule (verifier != enterer) that arrives with the lab module at
    # milestone 7, along with the LabResult model it attaches to.
    #
    # Asserting it at role level forced a role split real laboratories do not
    # have, in order to satisfy a test that was checking the wrong thing.
    forbidden_pairs = [
        ("billing.waiver.request", "billing.waiver.approve", "asking for and granting a discount"),
        ("billing.payment.take", "billing.refund", "taking money and refunding it"),
    ]
    for first, second, why in forbidden_pairs:
        a = MATRIX.by_permission(first)
        b = MATRIX.by_permission(second)
        assert a and b
        both = (a.allow & b.allow) - {"owner"}  # the owner is the audited exception
        assert not both, (
            f"Separation of duties broken ({why}): {sorted(both)} hold both {first} and {second}"
        )


def test_clinical_notes_are_closed_to_money_roles() -> None:
    """
    The line the whole permission design exists to hold: a cashier and an
    accountant never see a clinical note.
    """
    entry = MATRIX.by_permission("clinical.note.read")
    assert entry is not None
    for role in ("cashier", "accountant", "store_keeper", "hr_officer"):
        assert role not in entry.allow, f"{role} must not be able to read clinical notes"


def test_matrix_coverage_is_reported() -> None:
    """
    Not a pass/fail on coverage — the matrix grows with each milestone — but
    the gap is printed so it stays visible.
    """
    uncovered = MATRIX.uncovered_permissions()
    covered = len(MATRIX.covered_permissions())
    total = len(ALL_PERMISSION_CODES)
    print(
        f"\npermissions matrix: {covered}/{total} codes covered; "
        f"not yet covered: {sorted(uncovered)}"
    )
    # The foundations must at least cover everything they enforce today.
    assert covered >= 50, "The matrix has lost rows — it should only ever grow."


# --------------------------------------------------------------------------
# Layer 1 — role templates match the matrix
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("entry", "role", "expected"), MATRIX.pairs(), ids=_ids(MATRIX.pairs()))
def test_role_template_matches_matrix(entry, role: str, expected: bool) -> None:
    """
    The seeded role grants the code if and only if the matrix says so.

    This is the cheapest and strictest of the three layers: it has no database
    and no HTTP, and it fails the moment somebody edits a role template without
    updating the matrix (or vice versa).
    """
    granted = entry.permission in role_codes(role)
    if expected:
        assert granted, (
            f"The matrix says {role} may {entry.permission}, but the {role} role "
            f"template does not grant it."
        )
    else:
        assert not granted, (
            f"The matrix says {role} may NOT {entry.permission}, but the {role} "
            f"role template grants it. Either the role is too wide or the matrix "
            f"is out of date — decide which, deliberately."
        )


# --------------------------------------------------------------------------
# Layer 2 — the rule engine agrees
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _engine_hospital(django_db_setup, django_db_blocker):
    """
    One hospital with one member of staff per role, built once for the module.

    Building eighteen hospitals' worth of staff per test case would make this
    suite too slow to run on every push, and the point of a generated matrix is
    that it runs on every push.
    """
    with django_db_blocker.unblock():
        hospital = build_hospital(name="Matrix Test Hospital")
        staff = {role: hospital.staff(role) for role in MATRIX.roles}
        yield hospital, staff


@pytest.mark.parametrize(("entry", "role", "expected"), MATRIX.pairs(), ids=_ids(MATRIX.pairs()))
def test_rule_engine_matches_matrix(entry, role: str, expected: bool, _engine_hospital) -> None:
    """
    ``user.has_perm(code, obj)`` agrees with the matrix.

    The object is the hospital's own branch — a real, tenant-scoped row in the
    user's own branch, so the baseline predicates (active staff, same hospital,
    same branch) are all satisfied and what is left under test is the code
    check itself.
    """
    hospital, staff = _engine_hospital
    user = staff[role]
    user.__dict__.pop("_permission_cache", None)

    with hospital.scope():
        allowed = user.has_perm(entry.permission, hospital.main_branch)

    assert allowed is expected, (
        f"has_perm({entry.permission!r}) returned {allowed} for {role}, expected {expected}."
    )


# --------------------------------------------------------------------------
# Layer 3 — the real API agrees
# --------------------------------------------------------------------------

ENDPOINT_PAIRS = [
    (entry, role, expected)
    for entry, role, expected in MATRIX.pairs()
    if entry.endpoint is not None
]


@pytest.mark.parametrize(("entry", "role", "expected"), ENDPOINT_PAIRS, ids=_ids(ENDPOINT_PAIRS))
def test_endpoint_matches_matrix(entry, role: str, expected: bool, api) -> None:
    """
    Hit the actual endpoint and check the status code.

    A permission rule that is correct in the engine but not attached to the
    view protects nothing, and that mistake is invisible to layers 1 and 2.
    """
    hospital = build_hospital(name=f"Endpoint {role} {entry.permission}")
    user = hospital.staff(role)

    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )

    url = reverse(entry.endpoint.url_name, kwargs=entry.endpoint.kwargs or {})
    response = getattr(api, entry.endpoint.method.lower())(url)

    expected_status = entry.endpoint.allowed_status if expected else entry.endpoint.denied_status
    assert response.status_code == expected_status, (
        f"{entry.endpoint.method} {url} as {role} returned "
        f"{response.status_code}, expected {expected_status}. "
        f"Body: {getattr(response, 'data', b'')!r}"
    )
