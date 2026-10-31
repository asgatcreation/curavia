"""
The permission engine.

A staff member may do something only if **all four** of these agree:

    plan allows the module            (what the hospital pays for)
AND the hospital enabled it           (size mode and module switches)
AND their role grants the action      (coarse: "may verify lab results")
AND an object rule says yes           (fine: "...but only in your own branch")

Layers one to three live here. The fourth lives in ``apps/core/rules.py``,
built on django-rules predicates. Field-level masking is the ``MaskedFields``
serializer mixin at the bottom: a cashier's API response does not merely hide
the diagnosis on screen, it never contains it.

Every code below is a string because strings are what the permissions matrix
in ``docs/permissions-matrix.yml`` refers to, and that one file generates the
tests, the README table and the in-app "what can this role do?" screen.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from rest_framework import permissions

if TYPE_CHECKING:  # pragma: no cover
    from rest_framework.request import Request
    from rest_framework.views import APIView


@dataclass(frozen=True, slots=True)
class Permission:
    code: str
    label: str
    #: The module that must be enabled for this permission to mean anything.
    module: str | None = None
    #: True if granting this is a significant privilege, shown with a warning
    #: in the role editor.
    sensitive: bool = False


def _p(code: str, label: str, module: str | None = None, *, sensitive: bool = False) -> Permission:
    return Permission(code, label, module, sensitive)


PERMISSIONS: tuple[Permission, ...] = (
    # -- organisation and staff ---------------------------------------------
    _p("org.settings.view", "See hospital settings"),
    _p("org.settings.change", "Change hospital settings", sensitive=True),
    _p("org.branch.manage", "Create and edit branches"),
    _p("org.module.manage", "Switch modules on and off", sensitive=True),
    _p("staff.view", "See the staff list"),
    _p("staff.invite", "Invite staff"),
    _p("staff.manage", "Edit staff, roles and access", sensitive=True),
    _p("staff.deactivate", "Deactivate staff", sensitive=True),
    _p("role.manage", "Create and edit roles", sensitive=True),
    _p("branch.view_all", "See every branch, not just the active one", sensitive=True),
    # -- audit --------------------------------------------------------------
    _p("audit.view", "Read the audit log", sensitive=True),
    _p("audit.verify", "Verify the audit hash chain"),
    _p("emergency.break_glass", "Break-the-glass access to any record", sensitive=True),
    _p("emergency.review", "Review break-the-glass events", sensitive=True),
    # -- patients -----------------------------------------------------------
    _p("patients.view", "Open patient records", "patients"),
    _p("patients.create", "Register patients", "patients"),
    _p("patients.change", "Edit patient demographics", "patients"),
    _p("patients.merge", "Merge duplicate records", "patients", sensitive=True),
    _p("patients.view_all", "Open any patient, not only your own", "patients", sensitive=True),
    # -- clinical -----------------------------------------------------------
    _p("clinical.note.read", "Read clinical notes", "clinical"),
    _p("clinical.note.write", "Write clinical notes", "clinical"),
    _p("clinical.note.sign", "Sign and lock clinical notes", "clinical"),
    _p("clinical.note.amend", "Amend a signed note", "clinical", sensitive=True),
    _p(
        "clinical.sensitive.view",
        "Read sensitive fields (HIV status, psychiatric notes)",
        "clinical",
        sensitive=True,
    ),
    _p("clinical.order.create", "Place lab, imaging and medication orders", "clinical"),
    _p("clinical.referral.create", "Refer patients", "clinical"),
    # -- appointments and queues --------------------------------------------
    _p("appointment.view", "See the appointment diary", "scheduling"),
    _p("appointment.book", "Book and move appointments", "scheduling"),
    _p("appointment.cancel", "Cancel somebody's appointment", "scheduling"),
    _p("schedule.manage", "Edit clinics, rotas and leave", "scheduling", sensitive=True),
    _p("queue.manage", "Issue and call token numbers", "scheduling"),
    _p("journey.view", "See the live patient journey board", "scheduling"),
    # -- triage and nursing -------------------------------------------------
    _p("triage.vitals.record", "Record vitals and triage", "triage"),
    _p("triage.note.write", "Write nursing notes", "triage"),
    # -- laboratory ---------------------------------------------------------
    _p("lab.order.view", "See lab orders", "lab"),
    _p("lab.sample.collect", "Collect and label samples", "lab"),
    _p("lab.result.enter", "Enter results", "lab"),
    _p("lab.result.verify", "Verify results as a supervisor", "lab", sensitive=True),
    _p("lab.catalogue.manage", "Edit the test catalogue and reference ranges", "lab"),
    # -- radiology ----------------------------------------------------------
    _p("radiology.order.view", "See imaging orders", "radiology"),
    _p("radiology.report.write", "Write imaging reports", "radiology"),
    # -- pharmacy -----------------------------------------------------------
    _p("pharmacy.queue.view", "See the prescription queue", "pharmacy"),
    _p("pharmacy.dispense", "Dispense medication", "pharmacy"),
    _p(
        "pharmacy.override_warning",
        "Override an interaction or allergy warning",
        "pharmacy",
        sensitive=True,
    ),
    _p("pharmacy.controlled.record", "Record controlled drugs", "pharmacy", sensitive=True),
    _p("pharmacy.pos.sell", "Sell over the counter", "pharmacy"),
    # -- inventory ----------------------------------------------------------
    _p("inventory.view", "See stock", "inventory"),
    _p("inventory.receive", "Receive goods", "inventory"),
    _p("inventory.transfer", "Transfer stock between locations", "inventory"),
    _p("inventory.adjust", "Adjust stock and record wastage", "inventory", sensitive=True),
    _p("inventory.purchase.manage", "Raise purchase orders", "inventory"),
    # -- inpatient ----------------------------------------------------------
    _p("inpatient.admit", "Admit and discharge patients", "inpatient"),
    _p("inpatient.bed.manage", "Assign and transfer beds", "inpatient"),
    _p("inpatient.mar.administer", "Record medication administration", "inpatient"),
    # -- billing ------------------------------------------------------------
    _p("billing.invoice.view", "See bills", "billing"),
    _p("billing.invoice.change", "Edit bill lines", "billing"),
    _p("billing.payment.take", "Take payments", "billing"),
    _p("billing.refund", "Issue refunds", "billing", sensitive=True),
    _p("billing.waiver.request", "Request a waiver or discount", "billing"),
    _p("billing.waiver.approve", "Approve a waiver or discount", "billing", sensitive=True),
    _p("billing.price.manage", "Edit price lists and tariffs", "billing", sensitive=True),
    _p("billing.shift.reconcile", "Open, close and reconcile a cashier shift", "billing"),
    # -- payers -------------------------------------------------------------
    _p("payers.manage", "Manage HMOs and corporate clients", "payers"),
    _p("payers.claim.submit", "Build and submit claims", "payers"),
    # -- accounting ---------------------------------------------------------
    _p("accounting.view", "See financial reports", "accounting", sensitive=True),
    _p("accounting.expense.manage", "Record expenses and payables", "accounting"),
    # -- hr -----------------------------------------------------------------
    _p("hr.staff.manage", "Manage HR records and documents", "hr", sensitive=True),
    _p("hr.roster.manage", "Build rosters and approve leave", "hr"),
    # -- reporting ----------------------------------------------------------
    _p("reporting.view", "Open reports", "reporting"),
    _p("reporting.export", "Export data to Excel or CSV", "reporting", sensitive=True),
    # -- telemedicine -------------------------------------------------------
    _p("telemedicine.consult", "Hold video consultations", "telemedicine"),
    # -- platform level (Curavia's own staff) -------------------------------
    _p("platform.hospital.view", "See the list of hospitals"),
    _p("platform.hospital.verify", "Approve or reject hospital verification", sensitive=True),
    _p("platform.billing.manage", "Manage plans and subscriptions", sensitive=True),
    _p("platform.support.request_access", "Ask a hospital for support access", sensitive=True),
    _p("platform.announce", "Send announcements to hospitals"),
    _p("platform.metrics.view", "See platform metrics"),
)

PERMISSIONS_BY_CODE: dict[str, Permission] = {p.code: p for p in PERMISSIONS}
ALL_PERMISSION_CODES: frozenset[str] = frozenset(PERMISSIONS_BY_CODE)
SENSITIVE_CODES: frozenset[str] = frozenset(p.code for p in PERMISSIONS if p.sensitive)

#: Codes that only Curavia's own staff may ever hold.
PLATFORM_CODES: frozenset[str] = frozenset(
    p.code for p in PERMISSIONS if p.code.startswith("platform.")
)


def validate_codes(codes: set[str] | frozenset[str] | list[str]) -> None:
    unknown = set(codes) - ALL_PERMISSION_CODES
    if unknown:
        raise ValueError(f"Unknown permission codes: {sorted(unknown)}")


def module_for(code: str) -> str | None:
    permission = PERMISSIONS_BY_CODE.get(code)
    return permission.module if permission else None


# --------------------------------------------------------------------------
# DRF permission classes
# --------------------------------------------------------------------------


class HasPermission(permissions.BasePermission):
    """
    Check one or more permission codes, plus the module gate behind them.

    Usage on a view::

        class LabResultViewSet(...):
            permission_classes = [HasPermission]
            required_permissions = ["lab.result.enter"]
            # or, per action:
            permission_map = {"verify": ["lab.result.verify"]}

    Object-level checks fall through to django-rules, so a view does not have
    to remember to call it: ``has_object_permission`` consults the same rule
    name as the code it just checked.
    """

    message = "Your role does not allow this."

    def _required(self, view: APIView) -> list[str]:
        action = getattr(view, "action", None)
        mapping: Mapping[str, Sequence[str]] = getattr(view, "permission_map", {}) or {}
        if action and action in mapping:
            return list(mapping[action])
        return list(getattr(view, "required_permissions", []) or [])

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False

        required = self._required(view)
        if not required:
            return True

        held: frozenset[str] = getattr(request, "tenant_permissions", frozenset())
        modules: frozenset[str] = getattr(request, "available_modules", frozenset())

        for code in required:
            if code not in held:
                return False
            module = module_for(code)
            if module and module not in modules:
                self.message = (
                    "That module is not switched on for this hospital, "
                    "or is not included in its plan."
                )
                return False

        # A hospital in read-only mode (unpaid) can still be read, never written.
        if request.method not in permissions.SAFE_METHODS:
            organisation = getattr(request, "organisation", None)
            if organisation is not None and organisation.is_read_only:
                self.message = (
                    "This hospital's subscription is not active, so the system is "
                    "read-only. Data can still be read and exported."
                )
                return False
        return True

    def has_object_permission(self, request: Request, view: APIView, obj: Any) -> bool:
        return all(request.user.has_perm(code, obj) for code in self._required(view))


class ModuleEnabled(permissions.BasePermission):
    """Gate a whole view on a module, where no single code fits."""

    message = "That module is not available for this hospital."

    def has_permission(self, request: Request, view: APIView) -> bool:
        required = getattr(view, "required_module", None)
        if not required:
            return True
        return required in getattr(request, "available_modules", frozenset())


class IsPlatformStaff(permissions.BasePermission):
    """
    Curavia's own staff.

    Holding a platform permission is not the same as being able to see patient
    data: that needs a live ``SupportAccessGrant`` from the hospital, which the
    object rules check separately.
    """

    message = "Platform staff only."

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_platform_staff)


# --------------------------------------------------------------------------
# Field-level masking
# --------------------------------------------------------------------------


class MaskedFields:
    """
    Serializer mixin that drops or masks fields the caller may not see.

    This is enforced on the server, not in the front end. A cashier's response
    for a patient contains the name and the MRN but no diagnosis at all, so
    there is nothing for a curious person to find in the network tab.

    Declare the rules on the serializer::

        class PatientSerializer(MaskedFields, serializers.ModelSerializer):
            permission_fields = {
                "clinical.sensitive.view": ["hiv_status", "genotype"],
            }
            masked_fields = {
                "patients.view_all": {"phone": "partial"},
            }

    ``permission_fields`` removes the field entirely without the code.
    ``masked_fields`` keeps it but obscures the value.
    """

    permission_fields: ClassVar[dict[str, list[str]]] = {}
    masked_fields: ClassVar[dict[str, dict[str, str]]] = {}

    def _held_codes(self) -> frozenset[str]:
        request = self.context.get("request")  # type: ignore[attr-defined]
        return getattr(request, "tenant_permissions", frozenset()) if request else frozenset()

    def get_fields(self):  # type: ignore[no-untyped-def]
        fields = super().get_fields()  # type: ignore[misc]
        held = self._held_codes()
        for code, names in self.permission_fields.items():
            if code not in held:
                for name in names:
                    fields.pop(name, None)
        return fields

    def to_representation(self, instance):  # type: ignore[no-untyped-def]
        data = super().to_representation(instance)  # type: ignore[misc]
        held = self._held_codes()
        for code, rules in self.masked_fields.items():
            if code in held:
                continue
            for name, style in rules.items():
                if data.get(name):
                    data[name] = mask_value(str(data[name]), style)
        return data


def mask_value(value: str, style: str = "partial") -> str:
    """
    Obscure a value for a caller who may see that it exists but not what it is.

    ``partial`` keeps the last four characters, which is enough for a cashier
    to confirm a phone number a patient reads out without handing over a list
    of contactable patients.
    """
    if style == "full":
        return "•" * 8
    if style == "partial":
        tail = value[-4:]
        return f"{'•' * max(len(value) - 4, 3)}{tail}"
    if style == "initials":
        return " ".join(part[0].upper() + "." for part in value.split() if part)
    raise ValueError(f"Unknown mask style: {style!r}")
