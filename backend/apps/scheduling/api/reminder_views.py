"""
Reminders, and what they cost.

The cost endpoint is the reason this screen exists. Text messages are a few
naira each and email is free, so "shall we switch on SMS reminders?" is a
question about money that a hospital owner can only answer with a number in
front of them. Everything here is built to produce that number honestly,
including saying how many patients have no email address and would therefore
have to be texted.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.patients.models import Patient
from apps.scheduling import reminders
from apps.scheduling.models import NoShowRecord, ReminderJob


class ReminderSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    patient_mrn = serializers.CharField(source="patient.mrn", read_only=True)
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    appointment_at = serializers.DateTimeField(source="appointment.starts_at", read_only=True)

    class Meta:
        model = ReminderJob
        fields = (
            "id",
            "patient_name",
            "patient_mrn",
            "kind",
            "kind_label",
            "status",
            "channel",
            "send_at",
            "sent_at",
            "attempts",
            "cost_kobo",
            "failure_reason",
            "appointment_at",
        )


class NoShowSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    patient_mrn = serializers.CharField(source="patient.mrn", read_only=True)

    class Meta:
        model = NoShowRecord
        fields = ("id", "patient_name", "patient_mrn", "missed_at", "was_reminded", "note")


class ReminderListView(CuraviaAPIView):
    """What is queued, what went out, and what failed."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["appointment.view"]

    @extend_schema(responses={200: OpenApiResponse(description="Reminders and their state")})
    def get(self, request: CuraviaRequest) -> Response:
        wanted = request.query_params.get("status") or ""
        rows = ReminderJob.objects.select_related("patient", "appointment")
        if wanted:
            rows = rows.filter(status=wanted)
        rows = rows.order_by("-send_at")[:100]

        # Counted over everything, not over the page: a hospital wants to
        # know how many failed, not how many failed in the first hundred.
        counts = {
            choice.value: ReminderJob.objects.filter(status=choice.value).count()
            for choice in ReminderJob.Status
        }
        spent = sum(
            ReminderJob.objects.filter(status=ReminderJob.Status.SENT).values_list(
                "cost_kobo", flat=True
            )
        )
        return Response(
            {
                "reminders": ReminderSerializer(rows, many=True).data,
                "counts": counts,
                "spent_kobo": spent,
                # So the screen can say "text messages are not switched on"
                # rather than showing an empty list that looks like a bug.
                "sms_enabled": sms_enabled(),
                "email_enabled": email_enabled(),
            }
        )


class ReminderCostView(CuraviaAPIView):
    """What the next month of reminders would cost."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["appointment.view"]

    @extend_schema(responses={200: OpenApiResponse(description="An estimate, in kobo")})
    def get(self, request: CuraviaRequest) -> Response:
        try:
            days = min(int(request.query_params.get("days", 30) or 30), 90)
        except ValueError:
            days = 30
        estimate = reminders.estimate_cost(
            organisation_id=request.organisation.pk,  # type: ignore[union-attr]
            days=days,
        )
        return Response(
            {**estimate, "sms_enabled": sms_enabled(), "email_enabled": email_enabled()}
        )


class NoShowListView(CuraviaAPIView):
    """Who did not come, and whether we had reminded them."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["appointment.view"]

    @extend_schema(responses={200: NoShowSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        rows = NoShowRecord.objects.select_related("patient").order_by("-missed_at")[:100]
        results = NoShowSerializer(rows, many=True).data
        if results:
            audit.record(
                "appointment.no_shows.view",
                actor=request.user,
                changes={"rows": len(results)},
                flags=[audit.AuditFlag.BULK],
                **as_audit_kwargs(),
            )
        return Response(results)


class OptOutView(CuraviaAPIView):
    """
    Record that a patient does not want reminders, or wants them again.

    Audited, because "we never told them" is the first thing said after a
    missed appointment and somebody has to be able to check it.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.change"]

    @extend_schema(
        request=None,
        responses={200: OpenApiResponse(description="Updated")},
        operation_id="patients_set_reminder_preference",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        body: Any = request.data if hasattr(request.data, "get") else {}
        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return Response(
                {"detail": "No such patient.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        opted_out = bool(body.get("opted_out"))
        patient.reminders_opted_out = opted_out
        patient.save(update_fields=["reminders_opted_out", "updated_at"])

        audit.record(
            "patient.reminders.opt_out" if opted_out else "patient.reminders.opt_in",
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            **as_audit_kwargs(),
        )
        return Response({"id": str(patient.pk), "reminders_opted_out": opted_out})


class SendNowView(CuraviaAPIView):
    """
    Send whatever is due, now.

    Here because the hosting plan has no background worker on the free tier
    (docs/00-decisions.md), so somebody has to be able to press it — and
    because being able to press it is how a hospital finds out its keys are
    wrong before a patient does.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["schedule.manage"]

    @extend_schema(request=None, responses={200: OpenApiResponse(description="What went out")})
    def post(self, request: CuraviaRequest) -> Response:
        outcome = reminders.send_due()
        audit.record(
            "reminders.send_now",
            actor=request.user,
            changes={"sent": outcome.sent, "failed": outcome.failed},
            **as_audit_kwargs(),
        )
        return Response(
            {
                "sent": outcome.sent,
                "failed": outcome.failed,
                "skipped": outcome.skipped,
                "cost_kobo": outcome.cost_kobo,
                "at": timezone.now(),
            }
        )


def sms_enabled() -> bool:
    """True only when Termii is actually configured. See config/settings."""
    return bool(getattr(settings, "TERMII_API_KEY", "")) and bool(
        getattr(settings, "TERMII_SENDER_ID", "")
    )


def email_enabled() -> bool:
    return bool(getattr(settings, "BREVO_API_KEY", "")) and bool(
        getattr(settings, "DEFAULT_FROM_EMAIL", "")
    )


__all__ = [
    "NoShowListView",
    "OptOutView",
    "ReminderCostView",
    "ReminderListView",
    "SendNowView",
    "email_enabled",
    "sms_enabled",
]
