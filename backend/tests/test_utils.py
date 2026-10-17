"""
The primitives.

Small functions, but two of them are load-bearing: money arithmetic that is
wrong by a kobo loses a hospital's trust, and primary keys that are guessable
let somebody walk another hospital's records.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

import pytest

from apps.core.modules import (
    ALL_MODULE_CODES,
    CORE_MODULE_CODES,
    SizeMode,
    defaults_for_size,
    resolve_enabled,
)
from apps.core.utils import format_naira, kobo_to_naira, naira_to_kobo, uuid7

# --------------------------------------------------------------------------
# uuid7
# --------------------------------------------------------------------------


def test_uuid7_is_a_valid_version_7_uuid() -> None:
    value = uuid7()
    assert isinstance(value, uuid.UUID)
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_uuid7_values_sort_by_creation_time() -> None:
    """
    Time ordering is the reason for choosing v7 over v4.

    A random UUID destroys index locality: every insert lands in a different
    page of the index. v7 keeps recent rows together, which matters once a
    hospital has a few million audit events.
    """
    first = uuid7()
    time.sleep(0.01)
    second = uuid7()
    assert str(first) < str(second)
    assert first.int < second.int


def test_uuid7_values_are_not_guessable() -> None:
    """
    Sequential integers in a URL tell an attacker how many patients a hospital
    has, and let them enumerate records. v7 keeps 74 bits of randomness.
    """
    batch = [uuid7() for _ in range(500)]
    assert len(set(batch)) == 500
    # Two UUIDs made in the same millisecond must still differ in the random part.
    a, b = uuid7(), uuid7()
    assert a != b


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("naira", "expected_kobo"),
    [
        ("0", 0),
        ("1", 100),
        ("1500.50", 150_050),
        ("0.01", 1),
        ("0.99", 99),
        ("1000000", 100_000_000),
        (Decimal("2500.75"), 250_075),
        (1500.50, 150_050),
        (0.1, 10),
        # The classic float trap: 0.1 + 0.2 is not 0.3 in binary.
        ("0.3", 30),
        ("-250.25", -25_025),
    ],
)
def test_naira_to_kobo(naira, expected_kobo: int) -> None:
    assert naira_to_kobo(naira) == expected_kobo


def test_float_input_does_not_lose_a_kobo() -> None:
    """
    A float arrives from a form and must not corrupt the amount.

    ``Decimal(1500.50)`` is 1500.4999999999999545..., so the conversion routes
    floats through ``Decimal(str(value))`` instead. Without that, every tenth
    bill would be a kobo short.
    """
    assert naira_to_kobo(1500.50) == 150_050
    assert naira_to_kobo(8.70) == 870
    assert naira_to_kobo(2.675) == 268  # half up, not banker's rounding


def test_rounding_is_half_up() -> None:
    """
    Nigerian invoicing convention, and what a cashier expects.

    Python's own ``round`` uses banker's rounding, which would turn 0.005 into
    0.00 — defensible in statistics, wrong on a receipt.
    """
    assert naira_to_kobo("0.005") == 1
    assert naira_to_kobo("0.015") == 2
    assert naira_to_kobo("0.004") == 0


@pytest.mark.parametrize(
    ("kobo", "expected"),
    [
        (0, "₦0.00"),
        (1, "₦0.01"),
        (100, "₦1.00"),
        (150_050, "₦1,500.50"),
        (100_000_000, "₦1,000,000.00"),
        (-25_025, "-₦250.25"),
    ],
)
def test_format_naira(kobo: int, expected: str) -> None:
    assert format_naira(kobo) == expected


def test_money_round_trips() -> None:
    for amount in ("0.01", "1500.50", "999999.99", "250.25"):
        kobo = naira_to_kobo(amount)
        assert kobo_to_naira(kobo) == Decimal(amount)


def test_a_refund_formats_with_the_sign_before_the_symbol() -> None:
    """What a cashier sees on a refund line."""
    assert format_naira(-150_050) == "-₦1,500.50"


# --------------------------------------------------------------------------
# Modules
# --------------------------------------------------------------------------


def test_a_solo_clinic_does_not_get_ward_screens() -> None:
    """
    The whole reason size modes exist: a one-doctor clinic should not open the
    app and find a bed board and a theatre list.
    """
    solo = defaults_for_size(SizeMode.SOLO)
    assert "inpatient" not in solo
    assert "theatre" not in solo
    assert "mortuary" not in solo
    assert {"patients", "scheduling", "clinical", "billing"} <= solo


def test_a_hospital_gets_the_ward_modules() -> None:
    hospital = defaults_for_size(SizeMode.HOSPITAL)
    assert {"inpatient", "theatre", "emergency", "radiology"} <= hospital


def test_core_modules_are_always_on() -> None:
    """A hospital cannot switch off patient records and keep using the system."""
    enabled = resolve_enabled(plan_modules=frozenset(), organisation_modules=set())
    assert enabled >= CORE_MODULE_CODES


def test_a_module_the_plan_excludes_stays_off_however_it_is_switched() -> None:
    """
    Both sides have to agree. A hospital enabling a module it has not paid for
    does not get it.
    """
    enabled = resolve_enabled(
        plan_modules={"patients", "clinical"},
        organisation_modules={"patients", "clinical", "theatre"},
    )
    assert "theatre" not in enabled


def test_dependencies_are_resolved() -> None:
    """
    Inventory needs pharmacy, and inpatient needs triage. A module left on
    without what it depends on would half-work, which is worse than off.
    """
    enabled = resolve_enabled(
        plan_modules=ALL_MODULE_CODES,
        organisation_modules={"inventory", "patients"},
    )
    assert "inventory" not in enabled

    enabled = resolve_enabled(
        plan_modules=ALL_MODULE_CODES,
        organisation_modules={"inventory", "pharmacy", "patients"},
    )
    assert {"inventory", "pharmacy"} <= enabled


def test_every_module_dependency_exists() -> None:
    from apps.core.modules import MODULES

    for module in MODULES:
        unknown = module.requires - ALL_MODULE_CODES
        assert not unknown, f"{module.code} requires unknown module(s) {sorted(unknown)}"


# --------------------------------------------------------------------------
# The permission registry
# --------------------------------------------------------------------------


def test_every_permission_names_a_real_module() -> None:
    """
    A permission gated on a module that does not exist would be permanently
    unreachable, and the mistake is invisible at runtime.
    """
    from apps.core.permissions import PERMISSIONS

    for permission in PERMISSIONS:
        if permission.module is None:
            continue
        assert permission.module in ALL_MODULE_CODES, (
            f"{permission.code} is gated on module {permission.module!r}, "
            f"which is not in the registry"
        )


def test_every_role_template_grants_only_real_permissions() -> None:
    """Enforced at import time too, but asserted here so the failure is readable."""
    from apps.accounts.role_templates import ROLE_TEMPLATES
    from apps.core.permissions import ALL_PERMISSION_CODES

    for template in ROLE_TEMPLATES:
        unknown = set(template.codes) - ALL_PERMISSION_CODES
        assert not unknown, f"{template.slug} grants unknown code(s) {sorted(unknown)}"


def test_hospital_roles_never_hold_platform_permissions() -> None:
    """
    A hospital role that could verify hospitals or manage plans would be able
    to act on Curavia's own business.
    """
    from apps.accounts.role_templates import ROLE_TEMPLATES
    from apps.core.permissions import PLATFORM_CODES

    for template in ROLE_TEMPLATES:
        overlap = set(template.codes) & PLATFORM_CODES
        assert not overlap, f"{template.slug} holds platform code(s) {sorted(overlap)}"


def test_platform_roles_never_hold_clinical_permissions() -> None:
    """
    The promise the platform level is sold on: our own staff cannot read
    patient data. Not even by holding a clinical permission code.
    """
    from apps.accounts.role_templates import PLATFORM_ROLE_TEMPLATES
    from apps.core.permissions import PLATFORM_CODES

    for template in PLATFORM_ROLE_TEMPLATES:
        non_platform = set(template.codes) - PLATFORM_CODES
        assert not non_platform, (
            f"platform role {template.slug} holds non-platform code(s) "
            f"{sorted(non_platform)} — it must not be able to touch hospital data"
        )
