"""
The SaaS business layer: plans, subscriptions and hospital verification.

These models are deliberately thin at milestone 2 — the platform console is
built out at milestone 12. What exists now is what the permission engine needs
in order to be honest: a plan decides which modules a hospital may use, and
``Organisation.available_modules`` already consults it.

One principle is settled here and not revisited: a hospital that stops paying
goes **read-only**. Its data is never deleted without notice and an export.
See ``Organisation.is_read_only``.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel, UUIDPrimaryKeyModel
from apps.core.modules import validate_codes
from apps.core.utils import format_naira


class Plan(UUIDPrimaryKeyModel, TimeStampedModel):
    """What a hospital can buy."""

    class Tier(models.TextChoices):
        SOLO = "solo", _("Solo clinic")
        CLINIC = "clinic", _("Clinic")
        HOSPITAL = "hospital", _("Hospital")
        GROUP = "group", _("Hospital group")

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=40, unique=True)
    tier = models.CharField(max_length=16, choices=Tier)
    description = models.TextField(blank=True)

    #: Money is always integer kobo. See apps.core.utils.
    monthly_price_kobo = models.BigIntegerField(default=0)
    annual_price_kobo = models.BigIntegerField(default=0)
    price_per_extra_branch_kobo = models.BigIntegerField(default=0)
    price_per_extra_staff_kobo = models.BigIntegerField(default=0)

    included_branches = models.PositiveIntegerField(default=1)
    included_staff = models.PositiveIntegerField(default=10)
    trial_days = models.PositiveIntegerField(default=30)

    modules = models.JSONField(default=list, blank=True)
    is_public = models.BooleanField(default=True, help_text=_("Shown on the pricing page"))
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = _("plan")
        ordering = ("sort_order", "monthly_price_kobo")

    def __str__(self) -> str:
        return f"{self.name} ({format_naira(self.monthly_price_kobo)}/month)"

    def clean(self) -> None:
        super().clean()
        validate_codes(set(self.modules))


class Subscription(UUIDPrimaryKeyModel, TimeStampedModel):
    """One hospital's current plan. One per Organisation."""

    class Status(models.TextChoices):
        TRIALING = "trialing", _("On free trial")
        ACTIVE = "active", _("Active")
        PAST_DUE = "past_due", _("Payment failed")
        GRACE = "grace", _("Grace period")
        CANCELLED = "cancelled", _("Cancelled")

    class Interval(models.TextChoices):
        MONTHLY = "monthly", _("Monthly")
        ANNUAL = "annual", _("Annual")

    organisation = models.OneToOneField(
        "core.Organisation", on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=16, choices=Status, default=Status.TRIALING, db_index=True)
    interval = models.CharField(max_length=16, choices=Interval, default=Interval.MONTHLY)

    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    #: After this, the hospital goes read-only. It is never deleted.
    grace_ends_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    #: Paystack's own identifiers. No card details ever touch our database.
    paystack_customer_code = models.CharField(max_length=80, blank=True)
    paystack_subscription_code = models.CharField(max_length=80, blank=True)

    extra_branches = models.PositiveIntegerField(default=0)
    extra_staff = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("subscription")

    def __str__(self) -> str:
        return f"{self.organisation} on {self.plan.name}"

    @property
    def is_paying(self) -> bool:
        return self.status in {self.Status.ACTIVE, self.Status.PAST_DUE, self.Status.GRACE}


class VerificationApplication(UUIDPrimaryKeyModel, TimeStampedModel):
    """
    A hospital proving it is a real, licensed facility before going live.

    While this is pending the hospital can explore with demo data, which is
    what stops verification feeling like a locked door.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SUBMITTED = "submitted", _("Submitted")
        CHANGES_REQUESTED = "changes_requested", _("Changes requested")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")

    organisation = models.ForeignKey(
        "core.Organisation", on_delete=models.CASCADE, related_name="verification_applications"
    )
    status = models.CharField(max_length=24, choices=Status, default=Status.DRAFT, db_index=True)

    cac_number = models.CharField(max_length=40, blank=True)
    moh_licence_number = models.CharField(max_length=60, blank=True)
    moh_state = models.CharField(max_length=60, blank=True)
    medical_director_name = models.CharField(max_length=160, blank=True)
    medical_director_mdcn = models.CharField(max_length=40, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verifications_reviewed",
    )
    reviewer_note = models.TextField(blank=True, help_text=_("Shown to the hospital"))
    internal_note = models.TextField(blank=True, help_text=_("Never shown to the hospital"))

    class Meta:
        verbose_name = _("verification application")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"verification for {self.organisation} ({self.status})"


class VerificationDocument(UUIDPrimaryKeyModel, TimeStampedModel):
    """
    A supporting document. Private storage, signed URLs only — these contain a
    medical director's personal registration details.
    """

    class Kind(models.TextChoices):
        CAC = "cac", _("CAC certificate")
        MOH_LICENCE = "moh_licence", _("State Ministry of Health facility licence")
        MDCN = "mdcn", _("Medical director's MDCN certificate")
        PREMISES = "premises", _("Premises registration")
        OTHER = "other", _("Other")

    application = models.ForeignKey(
        VerificationApplication, on_delete=models.CASCADE, related_name="documents"
    )
    kind = models.CharField(max_length=24, choices=Kind)
    file = models.FileField(upload_to="verification/%Y/%m/")
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        verbose_name = _("verification document")
        ordering = ("kind",)

    def __str__(self) -> str:
        return f"{self.get_kind_display()} for {self.application_id}"
