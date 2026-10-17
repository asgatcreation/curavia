"""
The module registry.

A solo clinic should not see ward and theatre screens it will never use, and a
hospital on the cheapest plan should not get the modules it has not paid for.
Both are answered here, by one list.

A module is available to a user only if the plan includes it AND the hospital
has it switched on. The navigation the API returns is built from the same
function the permission class uses, so the menu and the endpoints can never
disagree — a greyed-out menu item that still works is a security bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import models
from django.utils.translation import gettext_lazy as _


class SizeMode(models.TextChoices):
    """
    How much hospital a customer is.

    A Django ``TextChoices`` rather than a plain ``StrEnum`` so it can be used
    directly as a model field's ``choices`` while still behaving as a string
    enum in the module logic below.
    """

    SOLO = "solo", _("Solo clinic")
    CLINIC = "clinic", _("Clinic")
    HOSPITAL = "hospital", _("Hospital")


@dataclass(frozen=True, slots=True)
class Module:
    code: str
    label: str
    description: str
    #: Hospital size modes that get this module switched on by default.
    default_for: frozenset[SizeMode] = field(default_factory=frozenset)
    #: Modules that must be on for this one to work.
    requires: frozenset[str] = field(default_factory=frozenset)
    #: Always on; cannot be switched off.
    core: bool = False


_ALL = frozenset(SizeMode)
_CLINIC_UP = frozenset({SizeMode.CLINIC, SizeMode.HOSPITAL})
_HOSPITAL_ONLY = frozenset({SizeMode.HOSPITAL})


MODULES: tuple[Module, ...] = (
    Module(
        "patients",
        "Patients and records",
        "Registration, medical record numbers, allergies, consents, timeline.",
        _ALL,
        core=True,
    ),
    Module(
        "scheduling",
        "Appointments and queues",
        "Booking, doctor schedules, token numbers, the live journey board.",
        _ALL,
        core=True,
    ),
    Module(
        "clinical",
        "Consultation (EMR)",
        "SOAP notes, diagnoses, orders, referrals, clinical documents.",
        _ALL,
        core=True,
    ),
    Module(
        "billing",
        "Billing and cashier",
        "Price lists per payer, bills, payments, receipts, cashier shifts.",
        _ALL,
        core=True,
    ),
    Module(
        "triage",
        "Triage and nursing",
        "Vitals, NEWS2 early warning scores, nursing notes, task lists.",
        _ALL,
    ),
    Module(
        "lab",
        "Laboratory",
        "Test catalogue, samples with barcodes, results with critical flags.",
        _ALL,
    ),
    Module(
        "pharmacy",
        "Pharmacy",
        "Live prescription queue, interaction and allergy checks, dispensing.",
        _ALL,
    ),
    Module(
        "inventory",
        "Inventory and stores",
        "Batches, NAFDAC numbers, expiry, suppliers, purchase orders, transfers.",
        _CLINIC_UP,
        requires=frozenset({"pharmacy"}),
    ),
    Module(
        "radiology",
        "Radiology and imaging",
        "Imaging orders, scheduling, reports, private file uploads.",
        _CLINIC_UP,
    ),
    Module(
        "emergency",
        "Emergency / A&E",
        "Triage categories, rapid orders, resuscitation notes.",
        _HOSPITAL_ONLY,
    ),
    Module(
        "inpatient",
        "Inpatient and wards",
        "Admissions, bed board, ward rounds, medication administration record.",
        _HOSPITAL_ONLY,
        requires=frozenset({"triage"}),
    ),
    Module(
        "maternity",
        "Maternity and child health",
        "Antenatal, delivery, postnatal, the national immunisation schedule.",
        _CLINIC_UP,
    ),
    Module(
        "theatre",
        "Theatre and procedures",
        "Scheduling, surgical safety checklist, consent, procedure notes.",
        _HOSPITAL_ONLY,
    ),
    Module(
        "mortuary",
        "Mortuary and deaths",
        "Death certification, mortuary register and fees.",
        _HOSPITAL_ONLY,
    ),
    Module(
        "payers",
        "HMO, NHIA and corporate",
        "Enrolees, pre-authorisation, claims batches, corporate statements.",
        _CLINIC_UP,
    ),
    Module(
        "accounting",
        "Accounts",
        "Revenue by department and payer, expenses, payables, consultant fees.",
        _CLINIC_UP,
    ),
    Module(
        "hr",
        "HR and staff",
        "Profiles, professional licences with expiry alerts, rosters, leave.",
        _CLINIC_UP,
    ),
    Module(
        "assets",
        "Equipment and assets",
        "Equipment register, maintenance schedules, fault reports.",
        _HOSPITAL_ONLY,
    ),
    Module(
        "quality",
        "Quality and safety",
        "Incident reporting, complaints, patient satisfaction surveys.",
        _HOSPITAL_ONLY,
    ),
    Module(
        "messaging",
        "Internal messaging",
        "Secure staff chat, notification centre and preferences.",
        _ALL,
    ),
    Module(
        "telemedicine",
        "Online clinic",
        "Video consultations, async consults, e-prescriptions.",
        _ALL,
    ),
    Module(
        "portal",
        "Patient portal",
        "Appointments, results, prescriptions, bills, online payment.",
        _ALL,
    ),
    Module(
        "reporting",
        "Reports and analytics",
        "Census, wait times, revenue, top diagnoses, NHMIS summary export.",
        _ALL,
        core=True,
    ),
    Module(
        "fhir", "FHIR API", "Read-only HL7 FHIR R4 resources for interoperability.", frozenset()
    ),
)

MODULES_BY_CODE: dict[str, Module] = {m.code: m for m in MODULES}
ALL_MODULE_CODES: frozenset[str] = frozenset(MODULES_BY_CODE)
CORE_MODULE_CODES: frozenset[str] = frozenset(m.code for m in MODULES if m.core)


def defaults_for_size(size_mode: str | SizeMode) -> frozenset[str]:
    """The modules a new hospital of this size starts with."""
    mode = SizeMode(size_mode)
    return frozenset(m.code for m in MODULES if m.core or mode in m.default_for)


def resolve_enabled(
    *, plan_modules: frozenset[str] | set[str], organisation_modules: frozenset[str] | set[str]
) -> frozenset[str]:
    """
    The modules actually usable right now: in the plan, and switched on by the
    hospital, plus any core module (never switchable), plus the dependency
    closure so a module is never on without what it needs.
    """
    enabled = (set(plan_modules) & set(organisation_modules)) | set(CORE_MODULE_CODES)
    # Drop anything whose requirements are not met, repeatedly, until stable.
    while True:
        unsatisfied = {
            code
            for code in enabled
            if not MODULES_BY_CODE[code].requires <= enabled and not MODULES_BY_CODE[code].core
        }
        if not unsatisfied:
            return frozenset(enabled)
        enabled -= unsatisfied


def validate_codes(codes: set[str] | frozenset[str]) -> None:
    unknown = set(codes) - ALL_MODULE_CODES
    if unknown:
        raise ValueError(f"Unknown module codes: {sorted(unknown)}")
