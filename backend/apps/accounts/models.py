"""
Identity and access.

A ``User`` is one human being with one login, and may be a member of several
hospitals and several branches. That shape matters: a visiting consultant works
at three hospitals, a group's finance officer covers five branches, and giving
each of them three logins is how real systems end up with shared passwords.

``StaffMembership`` is the join, and it is where roles hang. It is also the
thing deactivation switches off — see ``signals.py``, which ends a deactivated
member's sessions and WebSocket connections immediately rather than waiting for
a cookie to expire.
"""

from __future__ import annotations

import uuid
from functools import lru_cache

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import PHONE_VALIDATOR, TenantScoped, TimeStampedModel, UUIDPrimaryKeyModel
from apps.core.permissions import PLATFORM_CODES, validate_codes
from apps.core.utils import uuid7


class UserManager(BaseUserManager["User"]):
    """Email is the identifier; there is no username to go stale."""

    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra) -> User:
        if not email:
            raise ValueError("A user must have an email address.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            # Invited staff set their own password; until then they cannot log in.
            user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str, **extra) -> User:
        extra.update(
            is_staff=True,
            is_superuser=True,
            is_platform_staff=True,
            email_verified_at=timezone.now(),
        )
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, UUIDPrimaryKeyModel):
    """One person, one login."""

    email = models.EmailField(unique=True, db_index=True)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    phone = models.CharField(max_length=24, blank=True, validators=[PHONE_VALIDATOR])
    title = models.CharField(max_length=20, blank=True, help_text=_("Dr, Prof, Mrs, Pharm…"))

    is_active = models.BooleanField(default=True, db_index=True)
    #: Django-admin access. Separate from ``is_platform_staff`` on purpose.
    is_staff = models.BooleanField(default=False)
    #: Curavia's own employees. Grants NO access to patient data by itself —
    #: that needs a SupportAccessGrant the hospital issues.
    is_platform_staff = models.BooleanField(default=False, db_index=True)

    email_verified_at = models.DateTimeField(null=True, blank=True)
    last_password_change = models.DateTimeField(default=timezone.now)
    date_joined = models.DateTimeField(default=timezone.now)

    #: Set when the user last switched branch, so the next login lands where
    #: they left off rather than on a branch picker every morning.
    last_organisation_id = models.UUIDField(null=True, blank=True)
    last_branch_id = models.UUIDField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = _("user")
        ordering = ("last_name", "first_name")

    def __str__(self) -> str:
        return self.full_name or self.email

    @property
    def full_name(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return f"{self.title} {name}".strip() if self.title else name

    def get_full_name(self) -> str:
        return self.full_name

    def get_short_name(self) -> str:
        return self.first_name

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    # -- memberships and permissions -------------------------------------

    def live_memberships(self):
        """Active memberships in live hospitals, with roles prefetched."""
        return (
            StaffMembership.unscoped.filter(user=self, is_active=True)
            .select_related("organisation")
            .prefetch_related("roles", "branches")
        )

    def has_live_membership(self, organisation_id: uuid.UUID | str) -> bool:
        return StaffMembership.unscoped.filter(
            user=self, organisation_id=organisation_id, is_active=True
        ).exists()

    def membership_for(self, organisation_id: uuid.UUID | str) -> StaffMembership | None:
        return (
            StaffMembership.unscoped.filter(
                user=self, organisation_id=organisation_id, is_active=True
            )
            .prefetch_related("roles", "branches")
            .first()
        )

    def permission_codes(self, organisation_id: uuid.UUID | str) -> frozenset[str]:
        """
        Every permission code this user holds in one hospital: the union of
        their roles there, plus their platform codes if they are our staff.

        Cached per instance for the life of the request. A single page can run
        dozens of object-level checks and they should not each hit the database.
        """
        cache: dict[str, frozenset[str]] = self.__dict__.setdefault("_permission_cache", {})
        key = str(organisation_id)
        if key not in cache:
            membership = self.membership_for(organisation_id)
            codes: set[str] = set()
            if membership:
                for role in membership.roles.all():
                    codes.update(role.permissions)
            if self.is_platform_staff:
                for platform_role in PlatformRole.objects.filter(users=self):
                    codes.update(platform_role.permissions)
            cache[key] = frozenset(codes)
        return cache[key]

    def deactivate(self, *, by=None) -> None:
        """
        Switch a person off everywhere at once.

        Their memberships stop granting anything on the very next request, and
        ``accounts.signals`` ends their sessions and sockets immediately.
        """
        self.is_active = False
        self.save(update_fields=["is_active"])
        StaffMembership.unscoped.filter(user=self).update(
            is_active=False, deactivated_at=timezone.now()
        )


class Role(TenantScoped):
    """
    A named set of permission codes, owned by one hospital and editable by it.

    Seeded from ``role_templates.py``. A hospital can rename, clone or build
    roles without us deploying anything.
    """

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=60)
    description = models.TextField(blank=True)
    permissions = models.JSONField(default=list, blank=True)
    #: True while the role still matches the template it was seeded from, so
    #: the setup wizard can safely update it. Cleared on first manual edit.
    from_template = models.BooleanField(default=False)
    is_protected = models.BooleanField(
        default=False, help_text=_("The owner role cannot be deleted or stripped of access.")
    )

    class Meta(TenantScoped.Meta):
        verbose_name = _("role")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "slug"], name="unique_role_slug_per_organisation"
            )
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        try:
            validate_codes(set(self.permissions))
        except ValueError as exc:
            raise ValidationError({"permissions": str(exc)}) from exc
        forbidden = set(self.permissions) & PLATFORM_CODES
        if forbidden:
            raise ValidationError(
                {
                    "permissions": _("A hospital role cannot hold platform permissions: %(codes)s")
                    % {"codes": ", ".join(sorted(forbidden))}
                }
            )


class PlatformRole(UUIDPrimaryKeyModel, TimeStampedModel):
    """A role for Curavia's own staff. Not tenant-scoped; there is one set."""

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=60, unique=True)
    description = models.TextField(blank=True)
    permissions = models.JSONField(default=list, blank=True)
    users = models.ManyToManyField(User, blank=True, related_name="platform_roles")

    class Meta:
        verbose_name = _("platform role")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        try:
            validate_codes(set(self.permissions))
        except ValueError as exc:
            raise ValidationError({"permissions": str(exc)}) from exc


class StaffMembership(TenantScoped):
    """
    One person's place in one hospital: which branches, which roles.

    A member can belong to several branches; the top-bar branch switcher moves
    between them, and ``branches`` is what the switcher is allowed to offer.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    branches = models.ManyToManyField("core.Branch", related_name="memberships")
    roles = models.ManyToManyField(Role, related_name="memberships")
    default_branch = models.ForeignKey(
        "core.Branch", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    job_title = models.CharField(max_length=120, blank=True)
    department = models.ForeignKey(
        "core.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="staff"
    )

    is_active = models.BooleanField(default=True, db_index=True)
    started_on = models.DateField(default=timezone.localdate)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantScoped.Meta):
        verbose_name = _("staff membership")
        ordering = ("user__last_name", "user__first_name")
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "user"], name="unique_membership_per_organisation"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} at {self.organisation}"

    @property
    def permission_codes(self) -> frozenset[str]:
        codes: set[str] = set()
        for role in self.roles.all():
            codes.update(role.permissions)
        return frozenset(codes)

    def may_use_branch(self, branch_id: uuid.UUID | str) -> bool:
        if "branch.view_all" in self.permission_codes:
            return True
        return self.branches.filter(pk=branch_id).exists()


class Invitation(TenantScoped):
    """
    An invitation for a staff member to join, accepted by setting a password
    and enrolling in MFA.

    The token is stored hashed: a leaked database row must not let somebody
    accept an invitation that was never theirs.
    """

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        REVOKED = "revoked", _("Revoked")
        EXPIRED = "expired", _("Expired")

    email = models.EmailField(db_index=True)
    first_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    roles = models.ManyToManyField(Role, related_name="invitations")
    branches = models.ManyToManyField("core.Branch", related_name="invitations")
    job_title = models.CharField(max_length=120, blank=True)

    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING, db_index=True)
    invited_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="invitations_sent"
    )
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta(TenantScoped.Meta):
        verbose_name = _("invitation")
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"invitation for {self.email}"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()


class UserSession(models.Model):
    """
    The device list, and the thing that makes "deactivating staff ends their
    sessions immediately" true rather than aspirational.

    Django's session table alone cannot answer "which sessions belong to this
    user?", so we keep the mapping and delete from both.
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    session_key = models.CharField(max_length=60, unique=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("user session")
        ordering = ("-last_seen_at",)

    def __str__(self) -> str:
        return f"{self.user} from {self.ip or 'unknown'}"


@lru_cache(maxsize=1)
def role_template_slugs() -> frozenset[str]:
    from .role_templates import ROLE_TEMPLATES

    return frozenset(template.slug for template in ROLE_TEMPLATES)
