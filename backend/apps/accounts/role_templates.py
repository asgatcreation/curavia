"""
The seeded role templates.

Roles in Curavia are **per-hospital, editable records**, not hard-coded
classes. A hospital that calls its front desk "Health Records" should be able
to rename it, and one where the matron also runs the store should be able to
build that role without waiting for us. These templates are only the starting
point a new hospital is seeded with.

Each role's codes answer one question: what does this person actually touch in
a day? A cashier gets billing and nothing clinical — not a greyed-out
diagnosis field, but a response that never contains a diagnosis. An accountant
gets money and no charts at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.core.permissions import validate_codes


@dataclass(frozen=True, slots=True)
class RoleTemplate:
    slug: str
    name: str
    description: str
    codes: tuple[str, ...]
    #: Shown first in the role picker; the roles most hospitals need.
    common: bool = True


ROLE_TEMPLATES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        "owner",
        "Owner / Medical director",
        "Full access to the hospital, including settings, staff and the audit log.",
        (
            "org.settings.view",
            "org.settings.change",
            "org.branch.manage",
            "org.module.manage",
            "staff.view",
            "staff.invite",
            "staff.manage",
            "staff.deactivate",
            "role.manage",
            "branch.view_all",
            "audit.view",
            "audit.verify",
            "emergency.review",
            "emergency.break_glass",
            "patients.view",
            "patients.create",
            "patients.change",
            "patients.merge",
            "patients.view_all",
            "clinical.note.read",
            "clinical.sensitive.view",
            "reporting.view",
            "reporting.export",
            "accounting.view",
            "billing.invoice.view",
            "billing.waiver.approve",
            "billing.price.manage",
            "payers.manage",
            "hr.staff.manage",
            "hr.roster.manage",
            "inventory.view",
        ),
    ),
    RoleTemplate(
        "facility_admin",
        "Facility administrator",
        "Runs the hospital day to day: staff, branches, prices and reports. No clinical notes.",
        (
            "org.settings.view",
            "org.branch.manage",
            "staff.view",
            "staff.invite",
            "staff.manage",
            "branch.view_all",
            "patients.view",
            "reporting.view",
            "reporting.export",
            "billing.invoice.view",
            "billing.price.manage",
            "billing.waiver.approve",
            "payers.manage",
            "inventory.view",
            "hr.roster.manage",
        ),
    ),
    RoleTemplate(
        "doctor",
        "Doctor",
        "Consultations, orders and referrals for their own patients and those referred to them.",
        (
            "patients.view",
            "patients.create",
            "patients.change",
            "clinical.note.read",
            "clinical.note.write",
            "clinical.note.sign",
            "clinical.note.amend",
            "clinical.order.create",
            "clinical.referral.create",
            "clinical.sensitive.view",
            "triage.vitals.record",
            "lab.order.view",
            "radiology.order.view",
            "inpatient.admit",
            "telemedicine.consult",
            "reporting.view",
            "emergency.break_glass",
        ),
    ),
    RoleTemplate(
        "consultant",
        "Consultant (visiting or locum)",
        "A doctor who also appears in fee-sharing. Same clinical access.",
        (
            "patients.view",
            "clinical.note.read",
            "clinical.note.write",
            "clinical.note.sign",
            "clinical.order.create",
            "clinical.referral.create",
            "clinical.sensitive.view",
            "lab.order.view",
            "radiology.order.view",
            "telemedicine.consult",
            "reporting.view",
        ),
        common=False,
    ),
    RoleTemplate(
        "nurse",
        "Nurse",
        "Triage, vitals, nursing notes and medication administration on their own ward.",
        (
            "patients.view",
            "triage.vitals.record",
            "triage.note.write",
            "clinical.note.read",
            "inpatient.mar.administer",
            "inpatient.bed.manage",
            "lab.sample.collect",
        ),
    ),
    RoleTemplate(
        "nurse_in_charge",
        "Nurse in charge",
        "A nurse who also manages the ward's beds, rosters and handovers.",
        (
            "patients.view",
            "triage.vitals.record",
            "triage.note.write",
            "clinical.note.read",
            "inpatient.mar.administer",
            "inpatient.bed.manage",
            "inpatient.admit",
            "lab.sample.collect",
            "hr.roster.manage",
            "branch.view_all",
        ),
        common=False,
    ),
    RoleTemplate(
        "front_desk",
        "Front desk / Health records officer",
        "Registers patients, books appointments and manages the queue. No clinical notes.",
        (
            "patients.view",
            "patients.create",
            "patients.change",
            "patients.merge",
            "billing.invoice.view",
        ),
    ),
    RoleTemplate(
        "lab_scientist",
        "Laboratory scientist",
        "Collects samples and enters results. Sees the demographics needed to interpret them.",
        (
            "patients.view",
            "lab.order.view",
            "lab.sample.collect",
            "lab.result.enter",
        ),
    ),
    RoleTemplate(
        "lab_supervisor",
        "Laboratory supervisor",
        "Verifies results before they reach the doctor, and manages the test catalogue.",
        (
            "patients.view",
            "lab.order.view",
            "lab.sample.collect",
            "lab.result.enter",
            "lab.result.verify",
            "lab.catalogue.manage",
            "reporting.view",
        ),
        common=False,
    ),
    RoleTemplate(
        "radiographer",
        "Radiographer / Radiologist",
        "Imaging orders, scheduling and reports.",
        ("patients.view", "radiology.order.view", "radiology.report.write"),
    ),
    RoleTemplate(
        "pharmacist",
        "Pharmacist",
        "Reviews and dispenses prescriptions, with the allergy and interaction checks.",
        (
            "patients.view",
            "clinical.note.read",
            "pharmacy.queue.view",
            "pharmacy.dispense",
            "pharmacy.override_warning",
            "pharmacy.controlled.record",
            "pharmacy.pos.sell",
            "inventory.view",
            "inventory.transfer",
        ),
    ),
    RoleTemplate(
        "pharmacy_technician",
        "Pharmacy technician",
        "Dispenses and sells over the counter. Cannot override a clinical warning.",
        (
            "patients.view",
            "pharmacy.queue.view",
            "pharmacy.dispense",
            "pharmacy.pos.sell",
            "inventory.view",
        ),
        common=False,
    ),
    RoleTemplate(
        "store_keeper",
        "Store keeper",
        "Receives goods, moves stock and raises purchase orders.",
        (
            "inventory.view",
            "inventory.receive",
            "inventory.transfer",
            "inventory.adjust",
            "inventory.purchase.manage",
        ),
    ),
    RoleTemplate(
        "cashier",
        "Cashier",
        "Takes payments and reconciles a shift. Sees bills and names, never a diagnosis.",
        (
            "patients.view",
            "billing.invoice.view",
            "billing.payment.take",
            "billing.waiver.request",
            "billing.shift.reconcile",
        ),
    ),
    RoleTemplate(
        "accountant",
        "Accountant",
        "Money only: revenue, expenses, payables and exports. No patient charts.",
        (
            "accounting.view",
            "accounting.expense.manage",
            "billing.invoice.view",
            "billing.refund",
            "billing.price.manage",
            "reporting.view",
            "reporting.export",
            "branch.view_all",
        ),
    ),
    RoleTemplate(
        "hmo_officer",
        "HMO / claims officer",
        "Enrolee checks, pre-authorisation codes and claim batches.",
        (
            "patients.view",
            "payers.manage",
            "payers.claim.submit",
            "billing.invoice.view",
            "reporting.view",
            "reporting.export",
        ),
    ),
    RoleTemplate(
        "hr_officer",
        "HR officer",
        "Staff records, professional licences, rosters and leave.",
        (
            "staff.view",
            "staff.invite",
            "hr.staff.manage",
            "hr.roster.manage",
            "branch.view_all",
        ),
    ),
    RoleTemplate(
        "auditor",
        "Auditor (read-only)",
        "Reads everything and changes nothing. Their own reads are audited too.",
        (
            "audit.view",
            "audit.verify",
            "emergency.review",
            "patients.view",
            "patients.view_all",
            "clinical.note.read",
            "lab.order.view",
            "radiology.order.view",
            "pharmacy.queue.view",
            "inventory.view",
            "billing.invoice.view",
            "accounting.view",
            "staff.view",
            "org.settings.view",
            "reporting.view",
            "branch.view_all",
        ),
    ),
)

ROLE_TEMPLATES_BY_SLUG: dict[str, RoleTemplate] = {r.slug: r for r in ROLE_TEMPLATES}

#: Platform-level roles, for Curavia's own staff. Deliberately separate: none
#: of these grants access to patient data, which needs a SupportAccessGrant
#: issued by the hospital.
PLATFORM_ROLE_TEMPLATES: tuple[RoleTemplate, ...] = (
    RoleTemplate(
        "platform_admin",
        "Platform administrator",
        "Runs the Curavia business: hospitals, plans, billing, announcements.",
        (
            "platform.hospital.view",
            "platform.hospital.verify",
            "platform.billing.manage",
            "platform.announce",
            "platform.metrics.view",
            "platform.support.request_access",
        ),
    ),
    RoleTemplate(
        "platform_support",
        "Platform support",
        "Answers tickets. Can ask a hospital for time-limited access, never take it.",
        ("platform.hospital.view", "platform.support.request_access"),
    ),
)


def _validate_templates() -> None:
    """Fail at import time if a template names a permission that does not exist."""
    for template in (*ROLE_TEMPLATES, *PLATFORM_ROLE_TEMPLATES):
        try:
            validate_codes(set(template.codes))
        except ValueError as exc:
            raise ValueError(f"Role template {template.slug!r}: {exc}") from exc


_validate_templates()
