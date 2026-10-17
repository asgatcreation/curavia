"""
Authentication.

MFA is mandatory for staff, so login is two steps rather than one: password
first, which returns a short-lived challenge token and nothing else, then the
six-digit code, which returns the real access and refresh tokens. A password
alone never yields a usable session for a staff account.

Every outcome is audited — successes, failures, lockouts and MFA failures. "Who
tried to get in, when, from where" is a question that gets asked after an
incident, and it cannot be answered retroactively.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import UserSession
from apps.accounts.signals import revoke_all_sessions
from apps.core import audit
from apps.core.api.types import CuraviaRequest
from apps.core.request_context import as_audit_kwargs

logger = logging.getLogger("curavia.audit")

MFA_CHALLENGE_SALT = "curavia.mfa.challenge"
MFA_CHALLENGE_MAX_AGE = 300  # five minutes to type six digits


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})


class MfaChallengeSerializer(serializers.Serializer):
    challenge = serializers.CharField()
    code = serializers.CharField(max_length=10)


def _issue_tokens(user: Any) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def _has_mfa(user: Any) -> bool:
    from allauth.mfa.models import Authenticator

    return Authenticator.objects.filter(user=user).exists()


def _verify_mfa_code(user: Any, code: str) -> bool:
    """
    Check a TOTP code or a recovery code.

    Delegated to allauth so the window, drift tolerance and single-use
    behaviour of recovery codes are its problem, not ours.
    """
    from allauth.mfa.models import Authenticator

    for authenticator in Authenticator.objects.filter(user=user):
        try:
            if authenticator.wrap().validate_code(code):
                return True
        except Exception:
            logger.debug("rejected an MFA code for authenticator %s", authenticator.pk)
            continue
    return False


class LoginView(APIView):
    """Step one: email and password."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_scope = "auth"

    @extend_schema(
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description="Signed in, or an MFA challenge"),
            401: OpenApiResponse(description="Wrong email or password"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()

        user = authenticate(request, username=email, password=serializer.validated_data["password"])
        if user is None:
            audit.record(
                "auth.login_failed",
                changes={"email": email},
                **as_audit_kwargs(),
            )
            # One message for both "no such account" and "wrong password", so
            # the endpoint cannot be used to find out who has an account here.
            return Response(
                {"detail": "That email and password do not match.", "code": "invalid_credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            audit.record(
                "auth.login_blocked", actor=user, reason="account deactivated", **as_audit_kwargs()
            )
            return Response(
                {"detail": "This account has been deactivated.", "code": "inactive"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if _has_mfa(user):
            signer = TimestampSigner(salt=MFA_CHALLENGE_SALT)
            audit.record("auth.mfa_challenged", actor=user, **as_audit_kwargs())
            return Response(
                {
                    "mfa_required": True,
                    "challenge": signer.sign(str(user.pk)),
                    "expires_in": MFA_CHALLENGE_MAX_AGE,
                }
            )

        # No authenticator enrolled yet. Staff must enrol before they can work,
        # which the front end enforces by sending them to setup; the tokens are
        # issued so they can reach that screen at all.
        audit.record("auth.login", actor=user, changes={"mfa": False}, **as_audit_kwargs())
        return Response({**_issue_tokens(user), "mfa_required": False, "mfa_enrolled": False})


class MfaChallengeView(APIView):
    """Step two: the six-digit code."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_scope = "auth"

    @extend_schema(
        request=MfaChallengeSerializer,
        responses={200: OpenApiResponse(description="Signed in")},
    )
    def post(self, request: Request) -> Response:
        serializer = MfaChallengeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        signer = TimestampSigner(salt=MFA_CHALLENGE_SALT)
        try:
            user_id = signer.unsign(
                serializer.validated_data["challenge"], max_age=MFA_CHALLENGE_MAX_AGE
            )
        except SignatureExpired:
            return Response(
                {
                    "detail": "That took too long. Please sign in again.",
                    "code": "challenge_expired",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BadSignature:
            return Response(
                {"detail": "That sign-in attempt is not valid.", "code": "bad_challenge"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.accounts.models import User

        user = User.objects.filter(pk=user_id, is_active=True).first()
        if user is None:
            return Response(
                {"detail": "That sign-in attempt is not valid.", "code": "bad_challenge"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _verify_mfa_code(user, serializer.validated_data["code"].strip()):
            audit.record("auth.mfa_failed", actor=user, **as_audit_kwargs())
            return Response(
                {"detail": "That code is not correct.", "code": "invalid_code"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        audit.record("auth.login", actor=user, changes={"mfa": True}, **as_audit_kwargs())
        return Response({**_issue_tokens(user), "mfa_required": False, "mfa_enrolled": True})


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True)


class RevokedSessionsSerializer(serializers.Serializer):
    sessions_ended = serializers.IntegerField()
    timeout_seconds = serializers.IntegerField()


class LogoutView(APIView):
    """Blacklist the refresh token and close this device's session."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=LogoutSerializer,
        responses={204: OpenApiResponse(description="Signed out")},
    )
    def post(self, request: CuraviaRequest) -> Response:
        # request.data is a Mapping for a JSON body; DRF types it as a union
        # because a multipart upload can arrive as a list.
        token = request.data.get("refresh") if hasattr(request.data, "get") else None
        if token:
            # An already-invalid or already-blacklisted token means the caller is
            # logged out, which is what they asked for. Not an error.
            with contextlib.suppress(Exception):
                RefreshToken(token).blacklist()

        session_key = getattr(request.session, "session_key", None)
        if session_key:
            UserSession.objects.filter(session_key=session_key).update(revoked_at=timezone.now())

        audit.record("auth.logout", actor=request.user, **as_audit_kwargs())
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = ("id", "ip", "user_agent", "created_at", "last_seen_at", "is_current")

    def get_is_current(self, obj: UserSession) -> bool:
        request = self.context.get("request")
        current = getattr(getattr(request, "session", None), "session_key", None)
        return bool(current and obj.session_key == current)


class SessionListView(APIView):
    """The device list. Session timeout is set by SESSION_COOKIE_AGE."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: SessionSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        sessions = UserSession.objects.filter(user=request.user, revoked_at__isnull=True)
        return Response(SessionSerializer(sessions, many=True, context={"request": request}).data)


class RevokeAllSessionsView(APIView):
    """
    Sign out everywhere.

    The same code path staff deactivation uses, so the two cannot drift —
    see ``accounts.signals.revoke_all_sessions``.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: RevokedSessionsSerializer})
    def post(self, request: CuraviaRequest) -> Response:
        count = revoke_all_sessions(request.user, reason="signed out everywhere by the user")
        return Response({"sessions_ended": count, "timeout_seconds": settings.SESSION_COOKIE_AGE})
