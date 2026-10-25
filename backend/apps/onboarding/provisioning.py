"""
Creating a hospital.

One function, one transaction. A half-created hospital — an organisation with
no owner, or an owner with no roles — is unrecoverable from the outside: there
is nobody who can sign in to fix it, because the only person who could is the
one whose membership failed to be written. So either all of it exists or none
of it does.

The order below is the dependency order, and the comments say why each step
cannot move.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.models import Role, StaffMembership, User
from apps.accounts.role_templates import ROLE_TEMPLATES
from apps.core.models import Branch, Department, Organisation, OrganisationStatus
from apps.core.modules import SizeMode
from apps.core.tenancy import unscoped
from apps.platformadmin.models import Plan, Subscription

#: Slug suffix length when the obvious slug is taken. Four hex characters is
#: 65,536 possibilities against a name that already exists, which is plenty
#: for a collision that is retried on failure anyway.
SLUG_SUFFIX_CHARS = 4

#: Departments every new hospital starts with, whatever its size. Deliberately
#: short: a clinic that gets eleven departments it has to delete is being made
#: to do work, and the setup wizard at milestone 4b offers the longer list.
STARTER_DEPARTMENTS: tuple[tuple[str, str, str], ...] = (
    ("General Outpatient", "GOPD", Department.Kind.CLINIC),
    ("Records / Front desk", "FD", Department.Kind.ADMIN),
)


class ProvisioningError(Exception):
    """Something was wrong with the request, not with us."""


@dataclass(frozen=True, slots=True)
class ProvisionedHospital:
    organisation: Organisation
    branch: Branch
    owner: User
    membership: StaffMembership
    subscription: Subscription


def unique_slug(name: str) -> str:
    """
    A URL-safe slug for a hospital name, unique across the platform.

    Organisation slugs are global rather than per-tenant — they are the tenant
    — so "St Mary's Hospital" in Lagos and another in Enugu cannot both be
    `st-marys-hospital`. The second gets a random suffix rather than a counter:
    a counter tells the world how many hospitals of that name are on Curavia,
    which is nobody's business.
    """
    base = slugify(name)[:50].strip("-") or "hospital"
    # slugify already strips most of it; this catches a name that was entirely
    # punctuation, which would otherwise produce an empty slug.
    base = re.sub(r"-{2,}", "-", base)

    if not Organisation.objects.filter(slug=base).exists():
        return base

    for _ in range(5):
        candidate = f"{base}-{secrets.token_hex(SLUG_SUFFIX_CHARS // 2)}"
        if not Organisation.objects.filter(slug=candidate).exists():
            return candidate

    # Five collisions in a row on a 16-bit suffix is not luck; something is
    # wrong. Better to fail than to loop.
    raise ProvisioningError("Could not find a free address for that hospital name.")


def seed_roles(organisation: Organisation) -> dict[str, Role]:
    """
    Create this hospital's own copy of the eighteen role templates.

    Copies, not references: roles are editable per hospital, so a hospital that
    renames "Front desk" to "Health Records" must not rename it for everybody
    else. The owner role is marked protected, which is what stops a hospital
    deleting the only role that can administer it.

    Used by provisioning AND by the test factories, so the two cannot drift.
    """
    roles: dict[str, Role] = {}
    with unscoped(reason="provisioning: this hospital has no scope until it exists"):
        for template in ROLE_TEMPLATES:
            roles[template.slug] = Role.unscoped.create(
                organisation=organisation,
                name=template.name,
                slug=template.slug,
                description=template.description,
                permissions=list(template.codes),
                from_template=True,
                is_protected=template.slug == "owner",
            )
    return roles


def default_plan(size_mode: str) -> Plan:
    """
    The plan a new hospital starts its trial on.

    Matched to the size the owner said they are, falling back to the cheapest
    public plan. A missing plan table means `manage.py seed_platform` was never
    run, which is a deployment fault and is reported as one rather than
    silently creating a hospital with no subscription.
    """
    plan = Plan.objects.filter(slug=size_mode).first()
    if plan is None:
        plan = Plan.objects.filter(is_public=True).order_by("monthly_price_kobo").first()
    if plan is None:
        raise ProvisioningError(
            "No subscription plans exist. Run `manage.py seed_platform` on this deployment."
        )
    return plan


@transaction.atomic
def provision_hospital(
    *,
    owner_email: str,
    owner_password: str,
    owner_first_name: str,
    owner_last_name: str,
    hospital_name: str,
    size_mode: str = SizeMode.CLINIC,
    branch_name: str = "Main Branch",
    branch_code: str = "MAIN",
    phone: str = "",
    state: str = "",
) -> ProvisionedHospital:
    """
    Create a hospital and the person who owns it.

    Atomic on purpose — see the module docstring. The steps are ordered by
    dependency:

    1. The **user** first, because the membership needs them and because a
       duplicate email must fail before anything else is written.
    2. The **organisation**, which everything below is scoped to.
    3. The **subscription**, so `available_modules()` has a plan to read. A
       hospital with no subscription has no modules and every screen is empty.
    4. The **branch**, marked main: branch-scoped rows need somewhere to live,
       and `resolve()` picks the main branch when the caller names none.
    5. The **roles**, before the membership, which points at one of them.
    6. The **membership**, which is what actually makes this person staff. Up
       to here the user exists but belongs to nothing.

    The organisation starts on a free trial rather than pending verification:
    CAC and MDCN checks happen before going live with real patients, not
    before the owner can look at the product. The status is what gates that,
    and the platform level at milestone 12 moves it.
    """
    email = owner_email.strip().lower()
    if User.objects.filter(email=email).exists():
        # Checked rather than caught, so the message is about the form field
        # the person is looking at rather than a database constraint.
        raise ProvisioningError("An account with that email already exists.")

    name = hospital_name.strip()
    if not name:
        raise ProvisioningError("The hospital needs a name.")

    owner = User.objects.create_user(
        email=email,
        password=owner_password,
        first_name=owner_first_name.strip(),
        last_name=owner_last_name.strip(),
        phone=phone.strip(),
    )

    organisation = Organisation.objects.create(
        name=name,
        slug=unique_slug(name),
        status=OrganisationStatus.TRIAL,
        size_mode=size_mode,
        contact_email=email,
        contact_phone=phone.strip(),
    )

    plan = default_plan(size_mode)
    now = timezone.now()
    trial_ends = now + timedelta(days=plan.trial_days)
    subscription = Subscription.objects.create(
        organisation=organisation,
        plan=plan,
        status=Subscription.Status.TRIALING,
        trial_ends_at=trial_ends,
        current_period_start=now,
        current_period_end=trial_ends,
    )

    with unscoped(reason="provisioning: creating the first rows of a new hospital"):
        branch = Branch.unscoped.create(
            organisation=organisation,
            name=branch_name.strip() or "Main Branch",
            code=(branch_code.strip() or "MAIN").upper()[:16],
            is_main=True,
            state=state.strip(),
            phone=phone.strip(),
        )
        for department_name, code, kind in STARTER_DEPARTMENTS:
            Department.unscoped.create(
                organisation=organisation,
                branch=branch,
                name=department_name,
                code=code,
                kind=kind,
            )

    roles = seed_roles(organisation)

    with unscoped(reason="provisioning: the owner's own membership"):
        membership = StaffMembership.unscoped.create(
            organisation=organisation,
            user=owner,
            default_branch=branch,
            job_title="Owner / Medical director",
        )
        membership.branches.set([branch])
        membership.roles.set([roles["owner"]])

    owner.last_organisation_id = organisation.pk
    owner.last_branch_id = branch.pk
    owner.save(update_fields=["last_organisation_id", "last_branch_id"])

    return ProvisionedHospital(
        organisation=organisation,
        branch=branch,
        owner=owner,
        membership=membership,
        subscription=subscription,
    )
