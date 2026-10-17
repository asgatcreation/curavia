"""Serializers for the core endpoints."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.core.audit import AuditEvent
from apps.core.models import Branch, Department, Organisation
from apps.core.modules import MODULES_BY_CODE
from apps.core.permissions import PERMISSIONS_BY_CODE
from apps.core.utils import format_naira


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ("id", "name", "code", "address", "state", "lga", "phone", "is_main")
        read_only_fields = fields


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ("id", "name", "code", "kind", "branch", "is_active")
        read_only_fields = fields


class OrganisationSerializer(serializers.ModelSerializer):
    modules = serializers.SerializerMethodField()
    is_read_only = serializers.BooleanField(read_only=True)

    class Meta:
        model = Organisation
        fields = (
            "id",
            "name",
            "slug",
            "status",
            "size_mode",
            "modules",
            "is_read_only",
            "share_records_across_branches",
        )
        read_only_fields = fields

    def get_modules(self, obj: Organisation) -> list[str]:
        return sorted(obj.available_modules())


class ModuleSerializer(serializers.Serializer):
    """The module catalogue, so the front end can label its own navigation."""

    code = serializers.CharField()
    # `label` is also an attribute of DRF's Field base class. Declaring a field
    # by that name works — DRF collects declared fields before Field.__init__
    # assigns its own — but the type checker sees a clash.
    label = serializers.CharField()  # type: ignore[assignment]
    description = serializers.CharField()
    enabled = serializers.BooleanField()


class PermissionCatalogueSerializer(serializers.Serializer):
    """One permission code, for the role editor."""

    code = serializers.CharField()
    label = serializers.CharField()  # type: ignore[assignment]  # see above
    module = serializers.CharField(allow_null=True)
    #: Shown with a warning in the role editor before an owner grants it.
    sensitive = serializers.BooleanField()
    group = serializers.CharField()


class MembershipSerializer(serializers.Serializer):
    """One of the hospitals this person works at — the branch switcher's data."""

    organisation_id = serializers.UUIDField()
    organisation_name = serializers.CharField()
    job_title = serializers.CharField(allow_blank=True)
    roles = serializers.ListField(child=serializers.CharField())
    branches = BranchSerializer(many=True)


class MeSerializer(serializers.Serializer):
    """
    Everything the staff app needs on boot: who you are, where you are, what
    you may do, and what to put in the navigation.

    Permissions and modules come from the same source the API enforces with, so
    a menu item the user can see is always a menu item that works. A greyed-out
    button that still has a live endpoint behind it is a security bug, not a
    cosmetic one.
    """

    id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    phone = serializers.CharField(allow_blank=True)
    is_platform_staff = serializers.BooleanField()
    has_mfa = serializers.BooleanField()

    organisation = OrganisationSerializer(allow_null=True)
    active_branch = BranchSerializer(allow_null=True)
    memberships = MembershipSerializer(many=True)

    permissions = serializers.ListField(child=serializers.CharField())
    modules = serializers.ListField(child=serializers.CharField())


class AuditEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()
    action_label = serializers.SerializerMethodField()

    class Meta:
        model = AuditEvent
        fields = (
            "id",
            "sequence",
            "at",
            "action",
            "action_label",
            "actor",
            "actor_name",
            "actor_role",
            "object_type",
            "object_id",
            "patient_id",
            "changes",
            "reason",
            "ip",
            "flags",
        )
        read_only_fields = fields

    def get_actor_name(self, obj: AuditEvent) -> str:
        return obj.actor.full_name if obj.actor else "System"

    def get_action_label(self, obj: AuditEvent) -> str:
        permission = PERMISSIONS_BY_CODE.get(obj.action)
        return permission.label if permission else obj.action.replace(".", " ").capitalize()


class ChainVerificationSerializer(serializers.Serializer):
    """The answer to 'has anyone tampered with the audit log?'"""

    valid = serializers.BooleanField()
    event_count = serializers.IntegerField()
    head_hash = serializers.CharField()
    problem = serializers.CharField(allow_null=True)
    broken_at_sequence = serializers.IntegerField(allow_null=True)


class BranchSwitchSerializer(serializers.Serializer):
    branch = serializers.UUIDField()

    def validate_branch(self, value: Any) -> Any:
        membership = self.context["request"].membership
        if membership is None:
            raise serializers.ValidationError("You are not a member of any hospital.")
        if not membership.may_use_branch(value):
            # Deliberately the same message whether the branch does not exist
            # or belongs to another hospital: the error must not confirm that
            # some other hospital's branch id is real.
            raise serializers.ValidationError("You do not have access to that branch.")
        return value


def module_catalogue(enabled: frozenset[str]) -> list[dict[str, Any]]:
    return [
        {
            "code": module.code,
            "label": module.label,
            "description": module.description,
            "enabled": module.code in enabled,
        }
        for module in MODULES_BY_CODE.values()
    ]


def permission_catalogue() -> list[dict[str, Any]]:
    """
    Used by the role editor: every code, grouped, with the sensitive ones
    marked so an owner gets a warning before handing one out.
    """
    return [
        {
            "code": permission.code,
            "label": permission.label,
            "module": permission.module,
            "sensitive": permission.sensitive,
            "group": permission.code.split(".")[0],
        }
        for permission in PERMISSIONS_BY_CODE.values()
    ]


__all__ = [
    "AuditEventSerializer",
    "BranchSerializer",
    "BranchSwitchSerializer",
    "ChainVerificationSerializer",
    "DepartmentSerializer",
    "MeSerializer",
    "ModuleSerializer",
    "OrganisationSerializer",
    "PermissionCatalogueSerializer",
    "format_naira",
    "module_catalogue",
    "permission_catalogue",
]
