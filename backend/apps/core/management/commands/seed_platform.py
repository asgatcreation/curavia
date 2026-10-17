"""
Seed the records a fresh installation needs before anybody can sign up: the
subscription plans and Curavia's own platform roles.

Idempotent, so it is safe to run on every deploy.

Prices are illustrative and in integer kobo, like every other amount in the
system. A hospital on the "clinic" plan pays N45,000.00 a month, which is
stored as 4_500_000.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.accounts.models import PlatformRole
from apps.accounts.role_templates import PLATFORM_ROLE_TEMPLATES
from apps.core.modules import SizeMode, defaults_for_size
from apps.core.utils import format_naira
from apps.platformadmin.models import Plan

PLANS: list[dict[str, Any]] = [
    {
        "slug": "solo",
        "name": "Solo clinic",
        "tier": Plan.Tier.SOLO,
        "description": "One doctor, one location. Records, appointments and billing.",
        "monthly_price_kobo": 1_500_000,
        "annual_price_kobo": 15_000_000,
        "included_branches": 1,
        "included_staff": 5,
        "price_per_extra_staff_kobo": 300_000,
        "trial_days": 30,
        "size_mode": SizeMode.SOLO,
        "sort_order": 1,
    },
    {
        "slug": "clinic",
        "name": "Clinic",
        "tier": Plan.Tier.CLINIC,
        "description": "A clinic with its own laboratory and pharmacy.",
        "monthly_price_kobo": 4_500_000,
        "annual_price_kobo": 45_000_000,
        "included_branches": 1,
        "included_staff": 20,
        "price_per_extra_branch_kobo": 2_000_000,
        "price_per_extra_staff_kobo": 250_000,
        "trial_days": 30,
        "size_mode": SizeMode.CLINIC,
        "sort_order": 2,
    },
    {
        "slug": "hospital",
        "name": "Hospital",
        "tier": Plan.Tier.HOSPITAL,
        "description": "Wards, theatre, maternity and HMO claims as well.",
        "monthly_price_kobo": 12_000_000,
        "annual_price_kobo": 120_000_000,
        "included_branches": 2,
        "included_staff": 60,
        "price_per_extra_branch_kobo": 4_000_000,
        "price_per_extra_staff_kobo": 200_000,
        "trial_days": 30,
        "size_mode": SizeMode.HOSPITAL,
        "sort_order": 3,
    },
    {
        "slug": "group",
        "name": "Hospital group",
        "tier": Plan.Tier.GROUP,
        "description": "Several hospitals, one group dashboard, shared records.",
        "monthly_price_kobo": 35_000_000,
        "annual_price_kobo": 350_000_000,
        "included_branches": 6,
        "included_staff": 200,
        "price_per_extra_branch_kobo": 3_500_000,
        "price_per_extra_staff_kobo": 150_000,
        "trial_days": 30,
        "size_mode": SizeMode.HOSPITAL,
        "sort_order": 4,
    },
]


class Command(BaseCommand):
    help = "Create or update the subscription plans and the platform roles."

    def handle(self, *args: Any, **options: Any) -> None:
        for raw in PLANS:
            spec = dict(raw)
            size_mode = spec.pop("size_mode")
            slug = spec.pop("slug")
            modules = set(defaults_for_size(size_mode))
            if slug == "group":
                # The FHIR read API is a group-tier feature.
                modules.add("fhir")
            spec["modules"] = sorted(modules)

            plan, created = Plan.objects.update_or_create(slug=slug, defaults=spec)
            self.stdout.write(
                f"{'created' if created else 'updated'}  plan {plan.name} at "
                f"{format_naira(plan.monthly_price_kobo)}/month, "
                f"{len(plan.modules)} module(s)"
            )

        for template in PLATFORM_ROLE_TEMPLATES:
            role, created = PlatformRole.objects.update_or_create(
                slug=template.slug,
                defaults={
                    "name": template.name,
                    "description": template.description,
                    "permissions": list(template.codes),
                },
            )
            self.stdout.write(f"{'created' if created else 'updated'}  platform role {role.name}")

        self.stdout.write(self.style.SUCCESS("Platform seed complete."))
