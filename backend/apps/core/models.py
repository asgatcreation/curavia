"""
The tenant hierarchy and the abstract bases every other model inherits.

    Group          optional; separate legal companies under one owner.
                   Roll-up reporting only — NO data is shared across this line.
      Organisation THE TENANT. One hospital business = one customer =
                   one subscription = one data boundary.
        Branch     a physical facility: "Lifeline Ikeja", "Lifeline Lekki".

The single most consequential decision in Curavia is that the tenant boundary
is the Organisation, not the Branch. Patients therefore belong to the hospital
and can be shared between its branches (when the hospital enables it, audited),
while queues, stock, prices, staff rotas and cash stay per branch.

See docs/01-data-model.md §1.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .modules import SizeMode, defaults_for_size, resolve_enabled
from .tenancy import TenantManager, UnscopedManager
from .utils import uuid7

# --------------------------------------------------------------------------
# Abstract bases
# --------------------------------------------------------------------------


class UUIDPrimaryKeyModel(models.Model):
    """Time-ordered, unguessable primary keys. See apps.core.utils.uuid7."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """
    Who made this row and when, plus a soft-delete marker.

    ``archived_at`` rather than a hard DELETE: a clinical record must remain
    readable forever, and so must the audit trail that refers to it.
    """

    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        abstract = True

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self, *, by=None) -> None:
        self.archived_at = timezone.now()
        self.updated_by = by
        self.save(update_fields=["archived_at", "updated_by", "updated_at"])


class TenantScoped(UUIDPrimaryKeyModel, TimeStampedModel):
    """
    Base for anything belonging to one hospital.

    ``objects`` refuses to run without an active tenant scope; ``unscoped`` is
    the deliberate, grep-able escape hatch. ``base_manager_name`` points at
    ``unscoped`` so Django's internal forward-FK traversal does not explode —
    see apps.core.tenancy.UnscopedManager.
    """

    organisation = models.ForeignKey(
        "core.Organisation",
        on_delete=models.PROTECT,
        related_name="%(class)s_set",
        db_index=True,
        editable=False,
    )

    objects = TenantManager()
    unscoped = UnscopedManager()

    class Meta:
        abstract = True
        base_manager_name = "unscoped"
        default_manager_name = "objects"


class BranchScoped(TenantScoped):
    """Base for operational data that belongs to one physical facility."""

    branch = models.ForeignKey(
        "core.Branch",
        on_delete=models.PROTECT,
        related_name="%(class)s_set",
        db_index=True,
    )

    class Meta(TenantScoped.Meta):
        abstract = True


# --------------------------------------------------------------------------
# The hierarchy
# --------------------------------------------------------------------------

PHONE_VALIDATOR = RegexValidator(
    r"^\+?[0-9][0-9\s\-]{6,19}$",
    _("Enter a phone number, for example 08031234567 or +2348031234567."),
)


class Group(UUIDPrimaryKeyModel, TimeStampedModel):
    """
    An owner's holding record over several Organisations.

    Deliberately thin. It exists so a group dashboard can roll up numbers
    across separately incorporated hospitals. It grants NO access to patient
    data across the Organisation boundary — that boundary is the legal one.
    """

    name = models.CharField(max_length=200)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_groups"
    )

    class Meta:
        verbose_name = _("group")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class OrganisationStatus(models.TextChoices):
    TRIAL = "trial", _("Free trial")
    PENDING_VERIFICATION = "pending_verification", _("Pending verification")
    ACTIVE = "active", _("Active")
    PAST_DUE = "past_due", _("Payment overdue")
    GRACE = "grace", _("Grace period")
    READ_ONLY = "read_only", _("Read-only (unpaid)")
    SUSPENDED = "suspended", _("Suspended")
    CLOSED = "closed", _("Closed")


class Organisation(UUIDPrimaryKeyModel, TimeStampedModel):
    """
    One hospital business. The tenant.

    Not a ``TenantScoped`` model, obviously — it is the thing everything else
    is scoped to.
    """

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=60, unique=True)
    group = models.ForeignKey(
        Group, on_delete=models.SET_NULL, null=True, blank=True, related_name="organisations"
    )

    # Nigerian registration details, checked during verification.
    cac_number = models.CharField(
        max_length=40, blank=True, verbose_name=_("CAC registration number")
    )
    moh_licence_number = models.CharField(
        max_length=60, blank=True, verbose_name=_("State Ministry of Health facility licence")
    )
    medical_director_mdcn = models.CharField(
        max_length=40, blank=True, verbose_name=_("Medical director's MDCN number")
    )

    status = models.CharField(
        max_length=32, choices=OrganisationStatus, default=OrganisationStatus.TRIAL, db_index=True
    )
    size_mode = models.CharField(max_length=16, choices=SizeMode, default=SizeMode.CLINIC)
    enabled_modules = models.JSONField(default=list, blank=True)

    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=24, blank=True, validators=[PHONE_VALIDATOR])

    # Cross-branch record sharing is OFF until the hospital turns it on, and
    # every cross-branch read is audited with the `cross_branch` flag.
    share_records_across_branches = models.BooleanField(default=False)

    # Branding (white-label, Phase D) and per-hospital preferences.
    settings = models.JSONField(default=dict, blank=True)

    #: True for the seeded demo tenants, which are reset nightly. Real tenants
    #: are never touched by the reset job.
    is_demo = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = _("organisation")
        ordering = ("name",)
        indexes = [models.Index(fields=["status", "is_demo"])]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs) -> None:
        if not self.enabled_modules:
            self.enabled_modules = sorted(defaults_for_size(self.size_mode))
        super().save(*args, **kwargs)

    @property
    def is_read_only(self) -> bool:
        """
        A hospital that has not paid goes read-only. It is never deleted, and
        it can always export its data. See docs/00-decisions.md.
        """
        return self.status in {
            OrganisationStatus.READ_ONLY,
            OrganisationStatus.SUSPENDED,
            OrganisationStatus.CLOSED,
        }

    @property
    def is_live(self) -> bool:
        return self.status in {
            OrganisationStatus.TRIAL,
            OrganisationStatus.ACTIVE,
            OrganisationStatus.PAST_DUE,
            OrganisationStatus.GRACE,
        }

    def available_modules(self) -> frozenset[str]:
        """Modules usable right now: in the plan, and switched on here."""
        subscription = getattr(self, "subscription", None)
        plan_modules = frozenset(subscription.plan.modules) if subscription else frozenset()
        return resolve_enabled(
            plan_modules=plan_modules, organisation_modules=set(self.enabled_modules)
        )


class Branch(TenantScoped):
    """A physical facility. Queues, stock, prices, rotas and cash live here."""

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=16, help_text=_("Short code used in MRNs, e.g. IKJ"))
    address = models.TextField(blank=True)
    state = models.CharField(max_length=60, blank=True)
    lga = models.CharField(max_length=80, blank=True, verbose_name=_("Local government area"))
    phone = models.CharField(max_length=24, blank=True, validators=[PHONE_VALIDATOR])
    is_main = models.BooleanField(default=False)
    opened_on = models.DateField(null=True, blank=True)

    class Meta(TenantScoped.Meta):
        verbose_name = _("branch")
        verbose_name_plural = _("branches")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "code"], name="unique_branch_code_per_organisation"
            )
        ]

    def __str__(self) -> str:
        return self.name


class Department(BranchScoped):
    """A clinic or unit inside a branch: GOPD, Paediatrics, O&G, Dental…"""

    class Kind(models.TextChoices):
        CLINIC = "clinic", _("Outpatient clinic")
        WARD = "ward", _("Ward")
        THEATRE = "theatre", _("Theatre")
        LABORATORY = "laboratory", _("Laboratory")
        RADIOLOGY = "radiology", _("Radiology")
        PHARMACY = "pharmacy", _("Pharmacy")
        STORE = "store", _("Store")
        ADMIN = "admin", _("Administration")

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=24)
    kind = models.CharField(max_length=24, choices=Kind, default=Kind.CLINIC)
    is_active = models.BooleanField(default=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("department")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "code"], name="unique_department_code_per_branch"
            )
        ]

    def __str__(self) -> str:
        return self.name


# --------------------------------------------------------------------------
# Break the glass
# --------------------------------------------------------------------------


class EmergencyAccessGrant(TenantScoped):
    """
    Break-the-glass access to one patient's record.

    A doctor in A&E who has no treating relationship to a patient types a
    mandatory reason, gets access for a short window, and the medical director
    is notified immediately. Nobody is locked out of a dying patient's record,
    and nobody uses this casually.

    ``patient_id`` is a bare UUID rather than a foreign key because the Patient
    model arrives at milestone 5; the constraint is added then. The permission
    predicate that consults this table already works.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="emergency_grants"
    )
    patient_id = models.UUIDField(db_index=True)
    reason = models.TextField()
    granted_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    director_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantScoped.Meta):
        verbose_name = _("emergency access grant")
        ordering = ("-granted_at",)
        indexes = [models.Index(fields=["user", "patient_id", "expires_at"])]

    def __str__(self) -> str:
        return f"emergency access to {self.patient_id} until {self.expires_at:%Y-%m-%d %H:%M}"

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()


class SupportAccessGrant(TenantScoped):
    """
    The privacy guarantee at the platform level.

    Curavia's own staff see no patient data by default. A hospital grants a
    named platform user access for a limited time, with a reason. Everything
    done under the grant is audited into the hospital's own audit log, where
    the hospital can see it.
    """

    platform_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_grants"
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="support_grants_given"
    )
    reason = models.TextField()
    scope_note = models.CharField(
        max_length=200, blank=True, help_text=_("What the hospital asked for help with")
    )
    granted_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantScoped.Meta):
        verbose_name = _("support access grant")
        ordering = ("-granted_at",)

    def __str__(self) -> str:
        return f"support access for {self.platform_user} until {self.expires_at:%Y-%m-%d %H:%M}"

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()


# --------------------------------------------------------------------------
# Beds
# --------------------------------------------------------------------------


class Bed(BranchScoped):
    """
    One bed, in a ward.

    A ward is a ``Department`` with ``kind=WARD`` rather than a model of its
    own. That is deliberate: a ward already has everything a department has —
    a name, a code, a branch, staff assigned to it — and a parallel hierarchy
    would mean two places to look for "where in this hospital" and two sets of
    code to keep in step.

    The bed itself is a real row because the things a hospital asks of it are
    per-bed: is this one free, who is in it, how long has it been occupied,
    what does a night in it cost. A count on the ward cannot answer any of
    those.

    ``is_occupied`` is NOT a column. Occupancy is derived from admissions,
    which arrive with the inpatient module at milestone 10 — a stored flag
    would be a second source of truth for where a patient is, and the two
    would disagree the first time an admission was cancelled.
    """

    class Kind(models.TextChoices):
        GENERAL = "general", _("General ward bed")
        PRIVATE = "private", _("Private room")
        AMENITY = "amenity", _("Amenity / semi-private")
        ICU = "icu", _("Intensive care")
        HDU = "hdu", _("High dependency")
        LABOUR = "labour", _("Labour / delivery")
        RECOVERY = "recovery", _("Recovery")
        COT = "cot", _("Cot / incubator")
        DAY_CASE = "day_case", _("Day case")

    ward = models.ForeignKey(
        "core.Department",
        on_delete=models.PROTECT,
        related_name="beds",
        help_text=_("Must be a department of kind 'ward'."),
    )
    name = models.CharField(max_length=40, help_text=_("What is written on the bed, e.g. A3"))
    kind = models.CharField(max_length=16, choices=Kind, default=Kind.GENERAL)
    #: Integer kobo, like every other amount. Null means "not chargeable by
    #: the night" — a day-case trolley, for instance.
    nightly_rate_kobo = models.BigIntegerField(null=True, blank=True)
    is_active = models.BooleanField(
        default=True, help_text=_("Switch off a bed that is broken or being deep-cleaned.")
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta(BranchScoped.Meta):
        verbose_name = _("bed")
        ordering = ("ward__name", "name")
        constraints = [
            models.UniqueConstraint(fields=["ward", "name"], name="unique_bed_name_per_ward")
        ]

    def __str__(self) -> str:
        return f"{self.ward.name} · {self.name}"

    def clean(self) -> None:
        super().clean()
        # Checked here as well as in the serialiser: a bed in a laboratory is
        # the kind of mistake that is cheap to prevent and confusing to find
        # six months later on a bed board.
        if self.ward_id and self.ward.kind != Department.Kind.WARD:
            raise ValidationError(
                {
                    "ward": _("Beds belong to a ward. %(name)s is a %(kind)s.")
                    % {"name": self.ward.name, "kind": self.ward.get_kind_display().lower()}
                }
            )
        if self.ward_id and self.branch_id and self.ward.branch_id != self.branch_id:
            raise ValidationError({"ward": _("That ward is at a different branch from this bed.")})
