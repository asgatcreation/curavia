"""
The lists a new hospital is offered during setup.

Every one of these is a **starting point that gets copied into the hospital's
own rows**, never a shared reference. A hospital that renames "GOPD" to
"General Clinic", deletes the dental chair it does not have, or prices a
consultation at ₦7,500 instead of ₦5,000 must be able to, without that
affecting anybody else.

The prices are indicative mid-range private-hospital figures for Lagos in
2026, in integer kobo. They exist so that a hospital setting up at 9pm can
accept the defaults, start seeing patients, and adjust later — not because
Curavia knows what anybody should charge. The wizard says so on screen.

Sizes: each entry declares the smallest hospital it makes sense for, so a solo
practice is never offered a theatre list or a mortuary register.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.catalogue.models import Payer, ServiceCategory
from apps.core.models import Department
from apps.core.modules import SizeMode

# Which size modes an entry is offered to. Written as sets because the
# ordering of SizeMode is not a hierarchy you can compare with `>=`:
# a specialist eye clinic is "clinic" sized and still wants a theatre.
SOLO = {SizeMode.SOLO, SizeMode.CLINIC, SizeMode.HOSPITAL}
CLINIC_UP = {SizeMode.CLINIC, SizeMode.HOSPITAL}
HOSPITAL_ONLY = {SizeMode.HOSPITAL}


@dataclass(frozen=True, slots=True)
class DepartmentTemplate:
    name: str
    code: str
    kind: str
    sizes: frozenset[str]
    #: Suggested by default when the hospital accepts the list wholesale.
    common: bool = True


DEPARTMENT_TEMPLATES: tuple[DepartmentTemplate, ...] = (
    # --- clinics ---------------------------------------------------------
    DepartmentTemplate("General Outpatient", "GOPD", Department.Kind.CLINIC, frozenset(SOLO)),
    DepartmentTemplate("Paediatrics", "PAED", Department.Kind.CLINIC, frozenset(CLINIC_UP)),
    DepartmentTemplate(
        "Obstetrics & Gynaecology", "O&G", Department.Kind.CLINIC, frozenset(CLINIC_UP)
    ),
    DepartmentTemplate("Internal Medicine", "MED", Department.Kind.CLINIC, frozenset(CLINIC_UP)),
    DepartmentTemplate("Surgery", "SURG", Department.Kind.CLINIC, frozenset(CLINIC_UP)),
    DepartmentTemplate("Antenatal Clinic", "ANC", Department.Kind.CLINIC, frozenset(CLINIC_UP)),
    DepartmentTemplate("Immunisation", "IMM", Department.Kind.CLINIC, frozenset(CLINIC_UP)),
    DepartmentTemplate(
        "Dental", "DENT", Department.Kind.CLINIC, frozenset(CLINIC_UP), common=False
    ),
    DepartmentTemplate(
        "Eye Clinic", "EYE", Department.Kind.CLINIC, frozenset(CLINIC_UP), common=False
    ),
    DepartmentTemplate(
        "Physiotherapy", "PHYSIO", Department.Kind.CLINIC, frozenset(CLINIC_UP), common=False
    ),
    DepartmentTemplate(
        "Accident & Emergency", "A&E", Department.Kind.CLINIC, frozenset(HOSPITAL_ONLY)
    ),
    # --- wards -----------------------------------------------------------
    DepartmentTemplate("Male Ward", "MW", Department.Kind.WARD, frozenset(HOSPITAL_ONLY)),
    DepartmentTemplate("Female Ward", "FW", Department.Kind.WARD, frozenset(HOSPITAL_ONLY)),
    DepartmentTemplate("Children's Ward", "CW", Department.Kind.WARD, frozenset(HOSPITAL_ONLY)),
    DepartmentTemplate("Maternity Ward", "MAT", Department.Kind.WARD, frozenset(HOSPITAL_ONLY)),
    DepartmentTemplate(
        "Private Wing", "PRIV", Department.Kind.WARD, frozenset(HOSPITAL_ONLY), common=False
    ),
    DepartmentTemplate(
        "Intensive Care Unit", "ICU", Department.Kind.WARD, frozenset(HOSPITAL_ONLY), common=False
    ),
    # --- services --------------------------------------------------------
    DepartmentTemplate("Laboratory", "LAB", Department.Kind.LABORATORY, frozenset(CLINIC_UP)),
    DepartmentTemplate("Pharmacy", "PHARM", Department.Kind.PHARMACY, frozenset(CLINIC_UP)),
    DepartmentTemplate(
        "Radiology", "RAD", Department.Kind.RADIOLOGY, frozenset(CLINIC_UP), common=False
    ),
    DepartmentTemplate("Theatre", "THR", Department.Kind.THEATRE, frozenset(HOSPITAL_ONLY)),
    DepartmentTemplate(
        "Main Store", "STORE", Department.Kind.STORE, frozenset(CLINIC_UP), common=False
    ),
    DepartmentTemplate(
        "Administration", "ADMIN", Department.Kind.ADMIN, frozenset(SOLO), common=False
    ),
)


@dataclass(frozen=True, slots=True)
class ServiceTemplate:
    code: str
    name: str
    category: str
    #: Indicative self-pay price, integer kobo.
    price_kobo: int
    module: str
    sizes: frozenset[str]
    requires_prescription: bool = False
    common: bool = True


SERVICE_TEMPLATES: tuple[ServiceTemplate, ...] = (
    # --- consultations ---------------------------------------------------
    ServiceTemplate(
        "GOPD-NEW",
        "Consultation — new patient",
        ServiceCategory.CONSULTATION,
        5_000_00,
        "clinical",
        frozenset(SOLO),
    ),
    ServiceTemplate(
        "GOPD-REV",
        "Consultation — follow-up",
        ServiceCategory.CONSULTATION,
        3_000_00,
        "clinical",
        frozenset(SOLO),
    ),
    ServiceTemplate(
        "SPEC-NEW",
        "Specialist consultation — new",
        ServiceCategory.CONSULTATION,
        15_000_00,
        "clinical",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "SPEC-REV",
        "Specialist consultation — follow-up",
        ServiceCategory.CONSULTATION,
        10_000_00,
        "clinical",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "REG",
        "Registration and record card",
        ServiceCategory.ADMIN,
        2_000_00,
        "patients",
        frozenset(SOLO),
    ),
    # --- laboratory ------------------------------------------------------
    ServiceTemplate(
        "FBC", "Full blood count", ServiceCategory.LABORATORY, 6_500_00, "lab", frozenset(CLINIC_UP)
    ),
    ServiceTemplate(
        "MP-RDT",
        "Malaria parasite — rapid test",
        ServiceCategory.LABORATORY,
        2_500_00,
        "lab",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "MP-MIC",
        "Malaria parasite — microscopy",
        ServiceCategory.LABORATORY,
        3_500_00,
        "lab",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "URIN", "Urinalysis", ServiceCategory.LABORATORY, 3_000_00, "lab", frozenset(CLINIC_UP)
    ),
    ServiceTemplate(
        "FBS",
        "Fasting blood sugar",
        ServiceCategory.LABORATORY,
        2_500_00,
        "lab",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "EUCR",
        "Electrolytes, urea and creatinine",
        ServiceCategory.LABORATORY,
        12_000_00,
        "lab",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "LFT",
        "Liver function test",
        ServiceCategory.LABORATORY,
        14_000_00,
        "lab",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "PCV",
        "Packed cell volume",
        ServiceCategory.LABORATORY,
        1_500_00,
        "lab",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "HIV-SCR",
        "HIV screening",
        ServiceCategory.LABORATORY,
        3_000_00,
        "lab",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "HEP-BC",
        "Hepatitis B and C screening",
        ServiceCategory.LABORATORY,
        6_000_00,
        "lab",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "PREG", "Pregnancy test", ServiceCategory.LABORATORY, 2_000_00, "lab", frozenset(CLINIC_UP)
    ),
    ServiceTemplate(
        "WIDAL",
        "Widal test",
        ServiceCategory.LABORATORY,
        3_000_00,
        "lab",
        frozenset(CLINIC_UP),
        common=False,
    ),
    ServiceTemplate(
        "GXM",
        "Blood group and cross-match",
        ServiceCategory.LABORATORY,
        8_000_00,
        "lab",
        frozenset(HOSPITAL_ONLY),
        common=False,
    ),
    # --- imaging ---------------------------------------------------------
    ServiceTemplate(
        "CXR",
        "Chest X-ray",
        ServiceCategory.RADIOLOGY,
        18_000_00,
        "radiology",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "USS-ABD",
        "Abdominal ultrasound",
        ServiceCategory.RADIOLOGY,
        20_000_00,
        "radiology",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "USS-OBS",
        "Obstetric ultrasound",
        ServiceCategory.RADIOLOGY,
        18_000_00,
        "radiology",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "ECG",
        "Electrocardiogram",
        ServiceCategory.RADIOLOGY,
        15_000_00,
        "radiology",
        frozenset(CLINIC_UP),
        common=False,
    ),
    # --- nursing and procedures -----------------------------------------
    ServiceTemplate(
        "DRESS", "Wound dressing", ServiceCategory.NURSING, 3_500_00, "clinical", frozenset(SOLO)
    ),
    ServiceTemplate(
        "INJ",
        "Injection administration",
        ServiceCategory.NURSING,
        1_500_00,
        "clinical",
        frozenset(SOLO),
    ),
    ServiceTemplate(
        "IVF",
        "Intravenous infusion",
        ServiceCategory.NURSING,
        5_000_00,
        "clinical",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "SUT",
        "Suturing — minor",
        ServiceCategory.PROCEDURE,
        15_000_00,
        "clinical",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "NEB",
        "Nebulisation",
        ServiceCategory.NURSING,
        6_000_00,
        "clinical",
        frozenset(CLINIC_UP),
        common=False,
    ),
    # --- maternity and theatre ------------------------------------------
    ServiceTemplate(
        "ANC-REG",
        "Antenatal registration package",
        ServiceCategory.MATERNITY,
        60_000_00,
        "maternity",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "SVD",
        "Spontaneous vaginal delivery",
        ServiceCategory.MATERNITY,
        180_000_00,
        "maternity",
        frozenset(HOSPITAL_ONLY),
    ),
    ServiceTemplate(
        "CS",
        "Caesarean section",
        ServiceCategory.THEATRE,
        650_000_00,
        "theatre",
        frozenset(HOSPITAL_ONLY),
    ),
    ServiceTemplate(
        "HERNIA",
        "Herniorrhaphy",
        ServiceCategory.THEATRE,
        380_000_00,
        "theatre",
        frozenset(HOSPITAL_ONLY),
        common=False,
    ),
    # --- immunisation ----------------------------------------------------
    ServiceTemplate(
        "IMM-ROUT",
        "Routine immunisation (NPI schedule)",
        ServiceCategory.VACCINE,
        0,
        "maternity",
        frozenset(CLINIC_UP),
    ),
    ServiceTemplate(
        "IMM-PRIV",
        "Private vaccine administration",
        ServiceCategory.VACCINE,
        5_000_00,
        "maternity",
        frozenset(CLINIC_UP),
        common=False,
    ),
    # --- beds ------------------------------------------------------------
    ServiceTemplate(
        "BED-GEN",
        "General ward — per night",
        ServiceCategory.BED,
        25_000_00,
        "inpatient",
        frozenset(HOSPITAL_ONLY),
    ),
    ServiceTemplate(
        "BED-PRIV",
        "Private room — per night",
        ServiceCategory.BED,
        60_000_00,
        "inpatient",
        frozenset(HOSPITAL_ONLY),
    ),
    ServiceTemplate(
        "BED-ICU",
        "Intensive care — per night",
        ServiceCategory.BED,
        150_000_00,
        "inpatient",
        frozenset(HOSPITAL_ONLY),
        common=False,
    ),
    # --- administrative --------------------------------------------------
    ServiceTemplate(
        "MED-REP",
        "Medical report",
        ServiceCategory.ADMIN,
        15_000_00,
        "clinical",
        frozenset(SOLO),
        common=False,
    ),
    ServiceTemplate(
        "FIT-CERT",
        "Certificate of fitness",
        ServiceCategory.ADMIN,
        10_000_00,
        "clinical",
        frozenset(SOLO),
        common=False,
    ),
)


@dataclass(frozen=True, slots=True)
class PayerTemplate:
    """
    The schemes a Nigerian private hospital is most often asked to bill.

    Names only — no tariffs. A hospital's rates with an HMO are commercially
    confidential and negotiated per hospital, so Curavia must not ship a
    number and imply it is the rate. What this saves is the typing.
    """

    name: str
    code: str
    kind: str
    requires_preauthorisation: bool = True
    claim_window_days: int = 30
    common: bool = True


PAYER_TEMPLATES: tuple[PayerTemplate, ...] = (
    PayerTemplate(
        "Patient (self-pay)",
        "SELF",
        Payer.Kind.SELF,
        requires_preauthorisation=False,
        claim_window_days=0,
    ),
    PayerTemplate("NHIA", "NHIA", Payer.Kind.NHIA, claim_window_days=60),
    PayerTemplate("Hygeia HMO", "HYGEIA", Payer.Kind.HMO),
    PayerTemplate("Reliance Health", "RELIANCE", Payer.Kind.HMO),
    PayerTemplate("AXA Mansard Health", "AXAM", Payer.Kind.HMO),
    PayerTemplate("Leadway Health", "LEADWAY", Payer.Kind.HMO),
    PayerTemplate("Avon HMO", "AVON", Payer.Kind.HMO),
    PayerTemplate("Clearline HMO", "CLEARLINE", Payer.Kind.HMO, common=False),
    PayerTemplate("Police Health Maintenance", "PHML", Payer.Kind.HMO, common=False),
    PayerTemplate(
        "Corporate client",
        "CORP",
        Payer.Kind.CORPORATE,
        requires_preauthorisation=False,
        claim_window_days=45,
        common=False,
    ),
)


@dataclass(frozen=True, slots=True)
class WardTemplate:
    """A ward and how many beds of each kind to create in it."""

    department_code: str
    beds: tuple[tuple[str, int], ...] = field(default_factory=tuple)


#: Bed layouts suggested alongside the ward departments above. The counts are
#: a starting point for a mid-sized hospital; the wizard lets them be changed
#: before anything is written.
WARD_BED_TEMPLATES: tuple[WardTemplate, ...] = (
    WardTemplate("MW", (("general", 8),)),
    WardTemplate("FW", (("general", 8),)),
    WardTemplate("CW", (("cot", 4), ("general", 4))),
    WardTemplate("MAT", (("labour", 2), ("general", 6))),
    WardTemplate("PRIV", (("private", 6),)),
    WardTemplate("ICU", (("icu", 4),)),
)


def departments_for(size_mode: str, *, common_only: bool = False) -> list[DepartmentTemplate]:
    return [
        template
        for template in DEPARTMENT_TEMPLATES
        if size_mode in template.sizes and (template.common or not common_only)
    ]


def services_for(
    size_mode: str, enabled_modules: set[str], *, common_only: bool = False
) -> list[ServiceTemplate]:
    """
    Services this hospital could plausibly sell.

    Filtered by module as well as by size: a clinic that has not switched on
    radiology should not be offered an X-ray to price.
    """
    return [
        template
        for template in SERVICE_TEMPLATES
        if size_mode in template.sizes
        and (not template.module or template.module in enabled_modules)
        and (template.common or not common_only)
    ]


def payers_for(*, common_only: bool = False) -> list[PayerTemplate]:
    return [t for t in PAYER_TEMPLATES if t.common or not common_only]
