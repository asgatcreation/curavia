"""
Staff, roles and invitations.

The permission split here is the one an auditor looks for:

    staff.view        see who works here
    staff.invite      ask somebody to join
    staff.deactivate  end their access
    role.manage       change what a role can do

Inviting and deactivating are separate from each other and from role.manage
on purpose. Somebody who can invite staff but not edit roles cannot invent a
role that can do anything and then invite themselves into it — which is the
obvious escalation if the two were one permission.

The acceptance endpoint is public, because the person using it has no account
yet. It is the only endpoint in the system that creates a staff membership,
and it takes the roles and branches from the invitation rather than from the
request.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts import invitations as invites
from apps.accounts.models import Invitation, Role, StaffMembership
from apps.accounts.signals import revoke_all_sessions
from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.models import Branch
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs

# --------------------------------------------------------------------------
# Serialisers
# --------------------------------------------------------------------------


class RoleSerializer(serializers.ModelSerializer):
    permission_count = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ("id", "name", "slug", "description", "permission_count", "is_protected")

    def get_permission_count(self, obj: Role) -> int:
        return len(obj.permissions or [])


class StaffBranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ("id", "name", "code")


class StaffMemberSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    has_mfa = serializers.SerializerMethodField()
    roles = RoleSerializer(many=True, read_only=True)
    branches = StaffBranchSerializer(many=True, read_only=True)

    class Meta:
        model = StaffMembership
        fields = (
            "id",
            "full_name",
            "email",
            "job_title",
            "is_active",
            "started_on",
            "has_mfa",
            "roles",
            "branches",
        )

    def get_has_mfa(self, obj: StaffMembership) -> bool:
        """
        Shown on the staff list because it is an operational question, not a
        technical one: a hospital needs to see who has not finished enrolling
        and chase them, rather than discovering it when somebody is locked
        out mid-shift.
        """
        try:
            from allauth.mfa.models import Authenticator

            return Authenticator.objects.filter(user_id=obj.user_id).exists()
        except Exception:  # pragma: no cover - allauth.mfa optional at boot
            return False


class InvitationSerializer(serializers.ModelSerializer):
    roles = RoleSerializer(many=True, read_only=True)
    branches = StaffBranchSerializer(many=True, read_only=True)
    invited_by_name = serializers.SerializerMethodField()
    is_open = serializers.BooleanField(read_only=True)

    class Meta:
        model = Invitation
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "job_title",
            "status",
            "is_open",
            "expires_at",
            "accepted_at",
            "created_at",
            "roles",
            "branches",
            "invited_by_name",
        )
        # token_hash is deliberately absent. It is not secret in the way the
        # token is, but there is no reason for it to leave the database.

    def get_invited_by_name(self, obj: Invitation) -> str:
        return obj.invited_by.full_name if obj.invited_by else ""


class InviteRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    job_title = serializers.CharField(max_length=120, required=False, allow_blank=True)
    role_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    branch_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)


class AcceptRequestSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    first_name = serializers.CharField(max_length=80, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=80, required=False, allow_blank=True)

    def validate_password(self, value: str) -> str:
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value


class InvitationPreviewSerializer(serializers.Serializer):
    """What the acceptance page shows BEFORE anybody types a password."""

    hospital_name = serializers.CharField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    job_title = serializers.CharField()
    roles = serializers.ListField(child=serializers.CharField())
    invited_by = serializers.CharField()
    expires_at = serializers.DateTimeField()
    #: True when this address already has a Curavia account, so the page can
    #: say "sign in" rather than "choose a password".
    has_account = serializers.BooleanField()
    #: True when the invitation carries no name, so the page must ask for one
    #: rather than failing on submit.
    needs_name = serializers.BooleanField()


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------


class RoleListView(CuraviaAPIView):
    """The hospital's own roles, for the invite form and the role editor."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["staff.view"]

    @extend_schema(responses={200: RoleSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        return Response(RoleSerializer(Role.objects.all(), many=True).data)


class StaffListView(CuraviaAPIView):
    """Who works here, and who has not finished enrolling in MFA."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["staff.view"]

    @extend_schema(responses={200: StaffMemberSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        members = (
            StaffMembership.objects.select_related("user")
            .prefetch_related("roles", "branches")
            .all()
        )
        return Response(StaffMemberSerializer(members, many=True).data)


class InvitationListView(CuraviaAPIView):
    """List the invitations, and send a new one."""

    permission_classes = [IsAuthenticated, HasPermission]
    permission_map = {"get": ["staff.view"], "post": ["staff.invite"]}
    required_permissions = ["staff.view"]

    @extend_schema(responses={200: InvitationSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        rows = Invitation.objects.prefetch_related("roles", "branches").select_related("invited_by")
        return Response(InvitationSerializer(rows, many=True).data)

    @extend_schema(
        request=InviteRequestSerializer,
        responses={
            201: InvitationSerializer,
            400: OpenApiResponse(description="The invitation could not be sent"),
        },
    )
    def post(self, request: CuraviaRequest) -> Response:
        if "staff.invite" not in request.tenant_permissions:
            return Response(
                {"detail": "Your role does not allow this.", "code": "permission_denied"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = InviteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        organisation = request.organisation
        if organisation is None:
            return Response(
                {"detail": "You are not signed in to a hospital.", "code": "no_organisation"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            issued = invites.invite_staff(
                organisation=organisation,
                email=data["email"],
                role_ids=[str(value) for value in data["role_ids"]],
                branch_ids=[str(value) for value in data["branch_ids"]],
                invited_by=request.user,
                first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""),
                job_title=data.get("job_title", ""),
            )
        except invites.InvitationError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_invite"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invites.send_invitation_email(
            issued,
            hospital_name=organisation.name,
            inviter=request.user.full_name,
        )
        audit.record(
            "staff.invited",
            actor=request.user,
            object_type="Invitation",
            object_id=issued.invitation.pk,
            changes={
                "email": issued.invitation.email,
                "roles": [role.slug for role in issued.invitation.roles.all()],
            },
            **as_audit_kwargs(),
        )
        return Response(
            InvitationSerializer(issued.invitation).data, status=status.HTTP_201_CREATED
        )


class InvitationActionView(CuraviaAPIView):
    """Resend or revoke one invitation."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["staff.invite"]

    @extend_schema(
        # Named explicitly: without it this collides with the create endpoint
        # above, because drf-spectacular derives an operation id from the
        # path and both are a POST under /invitations/.
        operation_id="invitations_resend_or_revoke",
        request=None,
        responses={200: InvitationSerializer},
    )
    def post(self, request: CuraviaRequest, pk: str, action: str) -> Response:
        invitation = Invitation.objects.filter(pk=pk).first()
        if invitation is None:
            return Response(
                {"detail": "No such invitation.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        organisation = request.organisation
        try:
            if action == "resend":
                issued = invites.resend(invitation)
                invites.send_invitation_email(
                    issued,
                    hospital_name=organisation.name if organisation else "your hospital",
                    inviter=request.user.full_name,
                )
                recorded = "staff.invitation_resent"
            elif action == "revoke":
                invites.revoke(invitation)
                recorded = "staff.invitation_revoked"
            else:
                return Response(
                    {"detail": f"Unknown action {action!r}.", "code": "unknown_action"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except invites.InvitationError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_change"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit.record(
            recorded,
            actor=request.user,
            object_type="Invitation",
            object_id=invitation.pk,
            changes={"email": invitation.email},
            **as_audit_kwargs(),
        )
        invitation.refresh_from_db()
        return Response(InvitationSerializer(invitation).data)


class DeactivateStaffView(CuraviaAPIView):
    """
    End somebody's access, immediately.

    Not a delete. Their clinical notes, their prescriptions and their audit
    trail stay exactly where they are, attributed to them — a hospital that
    could erase who wrote a note could not answer the question an audit
    exists to answer.

    Every session ends at once. ``revoke_all_sessions`` is the same code path
    the signal uses, so "deactivated" and "signed out" cannot drift apart.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["staff.deactivate"]

    @extend_schema(request=None, responses={200: StaffMemberSerializer})
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        membership = StaffMembership.objects.filter(pk=pk).first()
        if membership is None:
            return Response(
                {"detail": "No such member of staff.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if membership.user_id == request.user.pk:
            # Not a safety rail for its own sake: an owner who deactivates
            # themselves locks the hospital out of its own settings, and
            # nobody inside it can undo that.
            return Response(
                {"detail": "You cannot deactivate your own account.", "code": "self_deactivate"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.utils import timezone

        membership.is_active = False
        membership.deactivated_at = timezone.now()
        membership.save(update_fields=["is_active", "deactivated_at"])
        revoke_all_sessions(membership.user, reason="staff membership deactivated")

        audit.record(
            "staff.deactivated",
            actor=request.user,
            object_type="StaffMembership",
            object_id=membership.pk,
            changes={"email": membership.user.email},
            **as_audit_kwargs(),
        )
        return Response(StaffMemberSerializer(membership).data)


class InvitationPreviewView(CuraviaAPIView):
    """
    What the acceptance page shows before anybody types anything.

    Public, and it reveals only what the holder of the token already needs to
    know: which hospital, who invited them, and what they were invited as.
    The token is unguessable, so this leaks nothing to anybody who does not
    already have it.
    """

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_scope = "auth"

    @extend_schema(
        responses={
            200: InvitationPreviewSerializer,
            404: OpenApiResponse(description="Not a valid invitation"),
        }
    )
    def get(self, request: Request) -> Response:
        from apps.accounts.models import User

        token = request.query_params.get("token", "")
        invitation = invites.find_open_invitation(token) if token else None
        if invitation is None:
            return Response(
                {
                    "detail": "That invitation link is not valid, or it has expired.",
                    "code": "bad_invitation",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Scoped to the hospital the token names: `invitation.roles` is a
        # related manager over a tenant-scoped model, and this caller is
        # anonymous with no scope of their own.
        from apps.core.tenancy import tenant_scope

        with tenant_scope(invitation.organisation_id):
            role_names = [role.name for role in invitation.roles.all()]

        return Response(
            {
                "hospital_name": invitation.organisation.name,
                "email": invitation.email,
                "first_name": invitation.first_name,
                "job_title": invitation.job_title,
                "roles": role_names,
                "invited_by": invitation.invited_by.full_name if invitation.invited_by else "",
                "expires_at": invitation.expires_at,
                "needs_name": not (invitation.first_name and invitation.last_name),
                "has_account": User.objects.filter(email=invitation.email, password__isnull=False)
                .exclude(password="")
                .filter(is_active=True)
                .exists(),
            }
        )


class AcceptInvitationView(CuraviaAPIView):
    """The only endpoint in the system that creates a staff membership."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_scope = "auth"

    @extend_schema(
        request=AcceptRequestSerializer,
        responses={
            200: OpenApiResponse(description="Joined; sign in next"),
            400: OpenApiResponse(description="Bad or expired invitation"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = AcceptRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            user = invites.accept(
                token=data["token"],
                password=data["password"],
                first_name=data.get("first_name", ""),
                last_name=data.get("last_name", ""),
            )
        except invites.InvitationError as exc:
            return Response(
                {"detail": str(exc), "code": "bad_invitation"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit.record(
            "staff.invitation_accepted",
            organisation_id=user.last_organisation_id,
            actor=user,
            **as_audit_kwargs(),
        )
        return Response(
            {
                "detail": "You have joined. Sign in with your email and new password.",
                "code": "joined",
                "email": user.email,
            }
        )
