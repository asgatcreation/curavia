"""
Test factories.

Every name here is **synthetic**. Nigerian names are used because the whole
point is that the system looks right to a Nigerian hospital, but no real
person's details appear anywhere in this repository, the tests or the demo.

``build_hospital`` is the workhorse: one call gives a complete, consistent
hospital with a plan, branches, departments and the eighteen seeded roles, so a
test can get straight to the thing it is actually checking.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

import factory
from django.utils import timezone

from apps.accounts.models import PlatformRole, Role, StaffMembership, User
from apps.accounts.role_templates import (
    PLATFORM_ROLE_TEMPLATES,
    ROLE_TEMPLATES_BY_SLUG,
)
from apps.core.models import (
    Branch,
    Department,
    Organisation,
    OrganisationStatus,
)
from apps.core.modules import ALL_MODULE_CODES, SizeMode
from apps.core.tenancy import tenant_scope, unscoped
from apps.onboarding.provisioning import seed_roles
from apps.platformadmin.models import Plan, Subscription

FIRST_NAMES = [
    "Adebayo",
    "Chiamaka",
    "Ifeoma",
    "Oluwaseun",
    "Ngozi",
    "Emeka",
    "Folake",
    "Yusuf",
    "Aisha",
    "Tunde",
    "Blessing",
    "Kelechi",
    "Hauwa",
    "Segun",
    "Amarachi",
    "Ibrahim",
    "Funmilayo",
    "Obinna",
    "Zainab",
    "Chidi",
]
LAST_NAMES = [
    "Okonkwo",
    "Adeyemi",
    "Balogun",
    "Eze",
    "Mohammed",
    "Okafor",
    "Afolabi",
    "Nwachukwu",
    "Bello",
    "Oyelaran",
    "Ogundipe",
    "Abubakar",
    "Ilesanmi",
    "Chukwuma",
    "Danjuma",
    "Oluwole",
    "Nnamdi",
    "Sanusi",
    "Akintola",
    "Udoka",
]


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    first_name = factory.Iterator(FIRST_NAMES)
    last_name = factory.Iterator(LAST_NAMES)
    email = factory.Sequence(lambda n: f"staff{n}@example.test")
    phone = factory.Sequence(lambda n: f"080{n:08d}"[:11])
    is_active = True
    email_verified_at = factory.LazyFunction(timezone.now)

    @factory.post_generation
    def password(obj, create: bool, extracted: str | None, **kwargs) -> None:
        if create:
            obj.set_password(extracted or "synthetic-test-password")
            obj.save(update_fields=["password"])


class PlanFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Plan
        django_get_or_create = ("slug",)

    name = "Hospital"
    slug = factory.Sequence(lambda n: f"hospital-{n}")
    tier = Plan.Tier.HOSPITAL
    monthly_price_kobo = 15_000_00  # ₦15,000.00
    included_branches = 5
    included_staff = 100
    # Everything on, so a permission test never fails for the wrong reason.
    modules = factory.LazyFunction(lambda: sorted(ALL_MODULE_CODES))


class OrganisationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organisation

    name = factory.Sequence(lambda n: f"Test Hospital {n}")
    slug = factory.Sequence(lambda n: f"test-hospital-{n}")
    status = OrganisationStatus.ACTIVE
    size_mode = SizeMode.HOSPITAL
    contact_email = factory.Sequence(lambda n: f"admin{n}@example.test")


@dataclass
class Hospital:
    """A fully wired hospital, ready for a test to act inside."""

    organisation: Organisation
    plan: Plan
    subscription: Subscription
    main_branch: Branch
    second_branch: Branch
    department: Department
    roles: dict[str, Role]

    def staff(
        self,
        role_slug: str,
        *,
        branch: Branch | None = None,
        branches: list[Branch] | None = None,
        **user_kwargs,
    ) -> User:
        """
        Create a member of staff holding one seeded role.

        Defaults to the main branch, which is what nearly every test wants.
        """
        if role_slug not in self.roles:
            raise KeyError(f"Unknown role {role_slug!r}. Known: {sorted(self.roles)}")
        user = UserFactory(**user_kwargs)
        chosen = branches if branches is not None else [branch or self.main_branch]
        with unscoped(reason="test setup: create a staff membership"):
            membership = StaffMembership.unscoped.create(
                organisation=self.organisation,
                user=user,
                default_branch=chosen[0],
                job_title=self.roles[role_slug].name,
            )
            membership.branches.set(chosen)
            membership.roles.set([self.roles[role_slug]])
        user.last_organisation_id = self.organisation.pk
        user.last_branch_id = chosen[0].pk
        user.save(update_fields=["last_organisation_id", "last_branch_id"])
        return user

    def destroy(self) -> None:
        """
        Delete everything this hospital created, in dependency order.

        Only needed by fixtures that build a hospital OUTSIDE pytest-django's
        per-test transaction — a module-scoped fixture using
        ``django_db_blocker.unblock()``. That data is committed and is NOT
        rolled back between tests, so without this it is visible to every
        later test in the run. It was: the isolation suite started seeing six
        branches where it had created four.

        The order matters because the foreign keys to Organisation are
        PROTECT, deliberately — a hospital's records must not vanish because
        something upstream was deleted.
        """
        from apps.core.audit import AuditChainHead, AuditEvent

        organisation = self.organisation
        with unscoped(reason="test teardown: remove a module-scoped hospital"):
            AuditEvent.objects.filter(organisation=organisation).delete()
            AuditChainHead.objects.filter(chain_key=f"org:{organisation.pk}").delete()

            memberships = StaffMembership.unscoped.filter(organisation=organisation)
            user_ids = list(memberships.values_list("user_id", flat=True))
            memberships.delete()

            User.objects.filter(pk__in=user_ids).delete()
            Role.unscoped.filter(organisation=organisation).delete()
            Department.unscoped.filter(organisation=organisation).delete()
            Branch.unscoped.filter(organisation=organisation).delete()
            Subscription.objects.filter(organisation=organisation).delete()
            organisation.delete()

    def scope(self, branch: Branch | None = None, *, all_branches: bool = False):
        """``with hospital.scope():`` — act inside this hospital."""
        return tenant_scope(
            self.organisation.pk,
            (branch or self.main_branch).pk,
            all_branches=all_branches,
        )


def build_hospital(
    *,
    name: str | None = None,
    status: str = OrganisationStatus.ACTIVE,
    size_mode: str = SizeMode.HOSPITAL,
    modules: list[str] | None = None,
) -> Hospital:
    """
    Create a complete hospital: plan, subscription, two branches, a department
    and all eighteen seeded roles.

    Two branches on purpose — one branch cannot catch a branch-scoping bug.
    """
    with unscoped(reason="test setup: build a hospital from scratch"):
        # The slug carries a random suffix even when a name is given.
        #
        # A deterministic slug collides with itself: a module-scoped fixture
        # that committed "matrix-test-hospital" and then crashed leaves that
        # row behind, and with --reuse-db every later run fails on the unique
        # constraint. Test data must not depend on the database being empty of
        # previous runs.
        overrides: dict[str, str] = {}
        if name:
            overrides["name"] = name
            slug_base = name.lower().replace(" ", "-")
            overrides["slug"] = f"{slug_base}-{uuid.uuid4().hex[:8]}"
        organisation = OrganisationFactory(
            **overrides,
            status=status,
            size_mode=size_mode,
        )
        if modules is not None:
            organisation.enabled_modules = modules
            organisation.save(update_fields=["enabled_modules"])

        plan = PlanFactory(modules=sorted(modules or ALL_MODULE_CODES))
        subscription = Subscription.objects.create(
            organisation=organisation,
            plan=plan,
            status=Subscription.Status.ACTIVE,
            current_period_end=timezone.now() + timedelta(days=30),
        )

        main = Branch.unscoped.create(
            organisation=organisation,
            name="Main Branch",
            code="MAIN",
            is_main=True,
            state="Lagos",
            lga="Ikeja",
        )
        second = Branch.unscoped.create(
            organisation=organisation,
            name="Second Branch",
            code="SEC",
            state="Lagos",
            lga="Eti-Osa",
        )
        department = Department.unscoped.create(
            organisation=organisation,
            branch=main,
            name="General Outpatient",
            code="GOPD",
            kind=Department.Kind.CLINIC,
        )

    # The same function provisioning uses, deliberately. A test hospital whose
    # roles were seeded differently from a real one would prove nothing about
    # a real one.
    roles = seed_roles(organisation)

    return Hospital(
        organisation=organisation,
        plan=plan,
        subscription=subscription,
        main_branch=main,
        second_branch=second,
        department=department,
        roles=roles,
    )


def build_platform_staff(role_slug: str = "platform_support") -> User:
    """
    One of Curavia's own employees.

    Holds platform permissions and, deliberately, no access to any hospital's
    patient data — that needs a SupportAccessGrant the hospital issues.
    """
    template = next(t for t in PLATFORM_ROLE_TEMPLATES if t.slug == role_slug)
    user = UserFactory(
        is_platform_staff=True, email=f"{role_slug}-{uuid.uuid4().hex[:6]}@curavia.test"
    )
    role, _created = PlatformRole.objects.get_or_create(
        slug=template.slug,
        defaults={
            "name": template.name,
            "description": template.description,
            "permissions": list(template.codes),
        },
    )
    role.users.add(user)
    return user


def role_codes(slug: str) -> frozenset[str]:
    """The permission codes a seeded role template grants."""
    return frozenset(ROLE_TEMPLATES_BY_SLUG[slug].codes)
