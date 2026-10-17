"""
Object-level permission rules.

Curavia uses django-rules rather than django-guardian, and the choice is worth
defending because it looks like a toss-up and is not.

Guardian stores a permission **row per (user, object)**. "Doctors see their own
patients" would mean a row per doctor per patient — millions of rows — and a
row would have to be written the instant a patient is referred and revoked the
instant they are discharged. Miss one of those writes and access either leaks
silently or breaks silently.

django-rules evaluates a **predicate** against current state. "Is this patient
on my ward right now?" is a question about the present, and hospital questions
almost always are. There is nothing to keep in sync, so there is nothing to
forget. Guardian's model is right for "share this one document with this one
person", which is not a problem Curavia has.

Predicates are deliberately tolerant of ``obj=None``: django-rules passes None
for a model-level check, and a view that has not fetched an object yet should
get the coarse answer rather than a crash.
"""

from __future__ import annotations

from typing import Any

import rules
from django.utils import timezone

from .permissions import ALL_PERMISSION_CODES, PLATFORM_CODES
from .tenancy import current_branch_id, current_organisation_id

# --------------------------------------------------------------------------
# Predicates
# --------------------------------------------------------------------------


@rules.predicate
def is_authenticated(user: Any) -> bool:
    return bool(user and user.is_authenticated)


@rules.predicate
def is_active_staff(user: Any) -> bool:
    """
    Authenticated, not deactivated, and holding a live membership in the
    hospital currently in scope.

    Deactivating a staff member therefore removes their access on the next
    request, without waiting for a session to expire — and
    ``accounts.signals`` kills their live sessions and sockets immediately.
    """
    if not (user and user.is_authenticated and user.is_active):
        return False
    organisation_id = current_organisation_id()
    if organisation_id is None:
        return False
    return user.has_live_membership(organisation_id)


@rules.predicate
def is_same_organisation(user: Any, obj: Any = None) -> bool:
    """
    The object belongs to the hospital in scope.

    This is belt and braces: the manager in ``apps.core.tenancy`` already makes
    it impossible to fetch another hospital's row through the ORM. It catches
    the case where an object arrives from somewhere else — a cache, a task
    argument, a hand-written query.
    """
    if obj is None:
        return current_organisation_id() is not None
    organisation_id = getattr(obj, "organisation_id", None)
    if organisation_id is None:
        return False
    return organisation_id == current_organisation_id()


@rules.predicate
def has_branch_view_all(user: Any) -> bool:
    return "branch.view_all" in _held(user)


@rules.predicate
def is_same_branch(user: Any, obj: Any = None) -> bool:
    """
    The object belongs to the branch the user is working in.

    Objects with no branch (a patient, which belongs to the hospital) pass:
    branch is not the question for them. Cross-branch patient access is
    governed by the hospital's own setting and audited separately.
    """
    if obj is None:
        return True
    branch_id = getattr(obj, "branch_id", None)
    if branch_id is None:
        return True
    if "branch.view_all" in _held(user):
        return True
    active = current_branch_id()
    return active is not None and branch_id == active


@rules.predicate
def is_own_record(user: Any, obj: Any = None) -> bool:
    """The user is the subject of this row — their own profile, their own leave."""
    if obj is None:
        return False
    for attribute in ("user_id", "staff_id", "owner_id"):
        if getattr(obj, attribute, None) == user.pk:
            return True
    return obj.pk == user.pk


@rules.predicate
def has_live_emergency_grant(user: Any, obj: Any = None) -> bool:
    """
    Break the glass.

    A doctor with no treating relationship to a patient may still open the
    record after typing a reason. The grant expires on its own, the medical
    director is notified, and the read is flagged ``emergency_access`` in the
    audit log.
    """
    if obj is None:
        return False
    patient_id = getattr(obj, "patient_id", None) or (
        obj.pk if type(obj).__name__ == "Patient" else None
    )
    if patient_id is None:
        return False
    from .models import EmergencyAccessGrant

    return EmergencyAccessGrant.objects.filter(
        user=user,
        patient_id=patient_id,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).exists()


@rules.predicate
def is_platform_support_with_grant(user: Any, obj: Any = None) -> bool:
    """
    Curavia's own staff, inside a window the hospital opened for them.

    Without a live grant this returns False, which is the whole point: the
    platform cannot read patient data by default, only when invited, only for
    as long as the invitation lasts, and always visibly.
    """
    if not getattr(user, "is_platform_staff", False):
        return False
    organisation_id = current_organisation_id()
    if organisation_id is None:
        return False
    from .models import SupportAccessGrant

    return SupportAccessGrant.objects.filter(
        platform_user=user,
        organisation_id=organisation_id,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).exists()


@rules.predicate
def organisation_is_writable(user: Any, obj: Any = None) -> bool:
    """False for a hospital in read-only mode because it has not paid."""
    from .models import Organisation

    organisation_id = current_organisation_id()
    if organisation_id is None:
        return False
    organisation = Organisation.objects.filter(pk=organisation_id).first()
    return bool(organisation and not organisation.is_read_only)


def _held(user: Any) -> frozenset[str]:
    """
    The permission codes the user holds in the hospital currently in scope.

    Cached on the user instance for the life of the request, because a single
    page can run dozens of object checks.
    """
    organisation_id = current_organisation_id()
    if organisation_id is None or not getattr(user, "is_authenticated", False):
        return frozenset()
    return user.permission_codes(organisation_id)


def has_code(code: str) -> Any:
    """Build a predicate that tests for one permission code."""

    @rules.predicate(f"has_code:{code}")
    def _predicate(user: Any) -> bool:
        return code in _held(user)

    return _predicate


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

#: The baseline every permission gets: active staff, same hospital, same
#: branch — or an open break-the-glass grant, or a support grant the hospital
#: issued. Specific codes tighten this below; later milestones add the
#: relational predicates (is_treating_doctor, is_in_my_ward, …) as the
#: clinical models arrive.
BASELINE = is_active_staff & is_same_organisation & is_same_branch

ESCALATED = has_live_emergency_grant | is_platform_support_with_grant


@rules.predicate
def is_platform_staff(user: Any) -> bool:
    return bool(getattr(user, "is_platform_staff", False))


#: Codes that are reads. Writes additionally require the hospital not to be in
#: read-only mode for non-payment.
_READ_SUFFIXES = frozenset({"view", "read", "verify", "view_all"})

#: Codes that are not limited to the active branch. An auditor who could only
#: see their own branch could not do the job; a patient belongs to the hospital
#: rather than to a branch.
_BRANCH_EXEMPT = frozenset(
    {
        "audit.view",
        "audit.verify",
        "emergency.review",
        "emergency.break_glass",
        "patients.view",
        "patients.create",
        "patients.change",
        "patients.merge",
        "patients.view_all",
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
        "reporting.view",
        "reporting.export",
        "accounting.view",
        "accounting.expense.manage",
        "payers.manage",
        "payers.claim.submit",
        "hr.staff.manage",
        "hr.roster.manage",
        "billing.price.manage",
    }
)


def _rule_for(code: str) -> Any:
    """
    Build the complete object rule for one permission code.

    Composed in one pass rather than registered and then amended, so the final
    rule for any code is readable here in one place.
    """
    if code in PLATFORM_CODES:
        # Platform codes are not tenant-scoped; IsPlatformStaff guards the view
        # and a SupportAccessGrant is what unlocks any patient data.
        return is_authenticated & is_platform_staff

    tenant_match = is_active_staff & is_same_organisation
    if code not in _BRANCH_EXEMPT:
        tenant_match = tenant_match & is_same_branch

    rule = (tenant_match | ESCALATED) & has_code(code)

    # Your own staff record is always visible to you, whatever your role.
    if code == "staff.view":
        rule = (tenant_match | ESCALATED) & (has_code(code) | is_own_record)

    if code.rsplit(".", 1)[-1] not in _READ_SUFFIXES:
        rule = rule & organisation_is_writable

    return rule


def register() -> None:
    """
    Register an object rule for every permission code.

    Registering *all* of them matters: a view can then never check a code that
    has no object rule behind it and quietly get a model-level pass. Later
    milestones tighten individual codes as the relational predicates
    (``is_treating_doctor``, ``is_in_my_ward``) become possible.
    """
    for code in sorted(ALL_PERMISSION_CODES):
        rules.set_perm(code, _rule_for(code))


register()
