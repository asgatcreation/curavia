"""
What "set up" means for a hospital.

Defined here, beside ``Organisation``, rather than in the onboarding app. The
onboarding app owns the *wizard* — the screens, the templates, the order
somebody is walked through them in — but whether a hospital is usable is a
property of the hospital itself, and ``/me`` has to answer it on every boot
without importing a wizard.

Progress is kept in ``Organisation.settings["setup"]``, not in columns. The
steps change as the product grows, and a JSON key is a change to this file
rather than a migration against every hospital's row. The cost is that it is
not queryable, which does not matter: nothing reports on it.

A step is marked done either because the hospital really has the rows (the
branch exists, a price list has entries) or because the owner explicitly said
"skip this". Skipping is recorded, so the wizard can stop asking without
anybody pretending the work was done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.core.models import Organisation

SETTINGS_KEY = "setup"


@dataclass(frozen=True, slots=True)
class SetupStep:
    key: str
    title: str
    blurb: str
    #: False for steps a hospital can legitimately finish without — HMO
    #: tariffs mean nothing to a clinic that only takes cash.
    required: bool = True


#: The seven steps of the wizard, in the order they are presented. The order is
#: dependency order: departments need a branch, prices need services, tariffs
#: need prices.
SETUP_STEPS: tuple[SetupStep, ...] = (
    SetupStep(
        "profile",
        "Your hospital",
        "Name, address, contact details and registration numbers.",
    ),
    SetupStep(
        "branches",
        "Branches",
        "Every location you run. You can add more later.",
    ),
    SetupStep(
        "departments",
        "Departments and clinics",
        "The units patients are seen in, from a list of the usual ones.",
    ),
    SetupStep(
        "wards",
        "Wards and beds",
        "Only if you admit patients.",
        required=False,
    ),
    SetupStep(
        "services",
        "Services and prices",
        "Consultations, procedures, tests — and what each one costs.",
    ),
    SetupStep(
        "payers",
        "HMOs and corporate clients",
        "Who you bill besides the patient, and at what tariff.",
        required=False,
    ),
    SetupStep(
        "staff",
        "Invite your staff",
        "They set their own password and enrol in two-factor authentication.",
        required=False,
    ),
)

SETUP_STEP_KEYS: tuple[str, ...] = tuple(step.key for step in SETUP_STEPS)
REQUIRED_STEP_KEYS: frozenset[str] = frozenset(s.key for s in SETUP_STEPS if s.required)


def _state(organisation: Organisation | None) -> dict[str, Any]:
    if organisation is None:
        return {}
    settings_blob = organisation.settings or {}
    state = settings_blob.get(SETTINGS_KEY) or {}
    return state if isinstance(state, dict) else {}


def completed_steps(organisation: Organisation | None) -> set[str]:
    """Steps the hospital has finished or deliberately skipped."""
    state = _state(organisation)
    done = state.get("done") or []
    skipped = state.get("skipped") or []
    known = set(SETUP_STEP_KEYS)
    return ({*done, *skipped}) & known


def setup_is_complete(organisation: Organisation | None) -> bool:
    """
    True once every required step is behind them.

    An organisation that does not exist — platform staff, or somebody signed
    in with no membership — is reported complete rather than incomplete, so
    nobody is sent into a setup wizard for a hospital they do not have.
    """
    if organisation is None:
        return True
    return completed_steps(organisation) >= REQUIRED_STEP_KEYS


def next_step(organisation: Organisation | None) -> str | None:
    """The first step not yet done, in wizard order, or None when finished."""
    done = completed_steps(organisation)
    for step in SETUP_STEPS:
        if step.key not in done:
            return step.key
    return None


def mark_step(organisation: Organisation, key: str, *, skipped: bool = False) -> None:
    """
    Record a step as done or skipped, and save.

    Writes the whole ``settings`` blob rather than a JSON path update: these
    rows are written once during setup and never concurrently, and a read-
    modify-write of a small dictionary is easier to be sure about than
    database-specific JSON operators.
    """
    if key not in SETUP_STEP_KEYS:
        raise ValueError(f"Unknown setup step {key!r}. Known: {list(SETUP_STEP_KEYS)}")

    settings_blob = dict(organisation.settings or {})
    state = dict(settings_blob.get(SETTINGS_KEY) or {})

    bucket = "skipped" if skipped else "done"
    other = "done" if skipped else "skipped"
    entries = [k for k in (state.get(bucket) or []) if k in SETUP_STEP_KEYS]
    if key not in entries:
        entries.append(key)
    state[bucket] = entries
    # A step cannot be both. Re-doing a skipped step clears the skip.
    state[other] = [k for k in (state.get(other) or []) if k != key and k in SETUP_STEP_KEYS]

    settings_blob[SETTINGS_KEY] = state
    organisation.settings = settings_blob
    organisation.save(update_fields=["settings"])
