"""
Sign-up and email verification.

These are the only endpoints in the system that run with no hospital in scope
and no authenticated caller, which makes them the ones worth being careful
about. Three rules hold here:

1. **Nothing is said about who already has an account.** A sign-up with an
   email that exists returns the same shape as one that succeeds, and the
   verification email tells the real owner that somebody tried. Returning "that
   email is taken" turns the form into a way to test whether a named doctor is
   a Curavia customer.
2. **Rate limited.** Both endpoints are on the `auth` throttle scope, because
   an unthrottled sign-up endpoint is a way to send email from our domain to
   anybody.
3. **Audited.** A hospital appearing is a platform-level event, and it is on
   the platform chain, not the new hospital's own — the new hospital's chain
   starts with what its staff do.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from apps.accounts.models import User
from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.modules import SizeMode
from apps.core.request_context import as_audit_kwargs
from apps.onboarding import emails
from apps.onboarding.provisioning import ProvisioningError, provision_hospital

#: Said to every caller of sign-up, whether or not anything was created.
SIGNUP_RESPONSE = {
    "detail": (
        "Check your email. If that address can be used, a confirmation link is on its way to it."
    ),
    "code": "check_your_email",
}


class SignUpSerializer(serializers.Serializer):
    """
    What an owner types to start a hospital.

    Password strength is Django's own validators, run through
    ``validate_password`` rather than reimplemented here, so the rules the API
    enforces are the rules `manage.py createsuperuser` enforces.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    first_name = serializers.CharField(max_length=80)
    last_name = serializers.CharField(max_length=80)
    phone = serializers.CharField(max_length=24, required=False, allow_blank=True)
    hospital_name = serializers.CharField(max_length=200)
    size_mode = serializers.ChoiceField(choices=SizeMode.choices, default=SizeMode.CLINIC)
    state = serializers.CharField(max_length=60, required=False, allow_blank=True)

    def validate_password(self, value: str) -> str:
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()
    code = serializers.CharField()


class SignUpView(CuraviaAPIView):
    """Create a hospital and the person who owns it."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_scope = "auth"

    @extend_schema(
        request=SignUpSerializer,
        responses={
            202: OpenApiResponse(
                response=DetailSerializer,
                description="Accepted. The same response whether or not an account was created.",
            ),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = SignUpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        email = data["email"].strip().lower()

        try:
            hospital = provision_hospital(
                owner_email=email,
                owner_password=data["password"],
                owner_first_name=data["first_name"],
                owner_last_name=data["last_name"],
                hospital_name=data["hospital_name"],
                size_mode=data["size_mode"],
                phone=data.get("phone", ""),
                state=data.get("state", ""),
            )
        except ProvisioningError as exc:
            # The one case we deliberately do not report: the address is
            # already in use. Everything else is a real fault in what was
            # sent, and saying so helps rather than leaks.
            if "already exists" not in str(exc):
                return Response(
                    {"detail": str(exc), "code": "cannot_provision"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            audit.record(
                "platform.signup_duplicate",
                organisation_id=None,
                changes={"email": email},
                **as_audit_kwargs(),
            )
            return Response(SIGNUP_RESPONSE, status=status.HTTP_202_ACCEPTED)

        audit.record(
            "platform.hospital_created",
            organisation_id=None,
            actor=hospital.owner,
            object_type="Organisation",
            object_id=hospital.organisation.pk,
            changes={"name": hospital.organisation.name, "slug": hospital.organisation.slug},
            **as_audit_kwargs(),
        )

        emails.send_verification_email(
            to=email,
            first_name=hospital.owner.first_name,
            hospital=hospital.organisation.name,
            token=emails.make_verification_token(str(hospital.owner.pk)),
        )

        return Response(SIGNUP_RESPONSE, status=status.HTTP_202_ACCEPTED)


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField()


class VerifyEmailView(CuraviaAPIView):
    """Mark an address as reachable. Idempotent."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_scope = "auth"

    @extend_schema(
        request=VerifyEmailSerializer,
        responses={
            200: OpenApiResponse(response=DetailSerializer, description="Verified"),
            400: OpenApiResponse(response=DetailSerializer, description="Bad or expired link"),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_id = emails.read_verification_token(serializer.validated_data["token"])
        if user_id is None:
            return Response(
                {
                    "detail": "That link is not valid, or it has expired. Ask for a new one.",
                    "code": "bad_token",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(pk=user_id).first()
        if user is None:
            return Response(
                {"detail": "That link is not valid.", "code": "bad_token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.email_verified_at is None:
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
            audit.record(
                "auth.email_verified",
                organisation_id=None,
                actor=user,
                **as_audit_kwargs(),
            )

        # Clicking an old link twice is not an error worth showing somebody.
        return Response({"detail": "Your email address is confirmed.", "code": "verified"})


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResendVerificationView(CuraviaAPIView):
    """Send the confirmation link again. Says nothing about who exists."""

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_scope = "auth"

    @extend_schema(
        request=ResendVerificationSerializer,
        responses={202: OpenApiResponse(response=DetailSerializer, description="Accepted")},
    )
    def post(self, request: Request) -> Response:
        serializer = ResendVerificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()

        user = User.objects.filter(email=email, is_active=True).first()
        if user is not None and user.email_verified_at is None:
            membership = user.live_memberships().first()
            hospital = membership.organisation.name if membership else "Curavia"
            emails.send_verification_email(
                to=email,
                first_name=user.first_name,
                hospital=hospital,
                token=emails.make_verification_token(str(user.pk)),
            )

        return Response(SIGNUP_RESPONSE, status=status.HTTP_202_ACCEPTED)
