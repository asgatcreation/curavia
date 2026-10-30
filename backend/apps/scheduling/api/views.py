"""
Appointments and queues over HTTP.

Two groups of endpoints with different audiences and different rhythms.

**The diary** — clinics, availability, book, move, cancel — is used by a
clerk with a patient in front of them, so every refusal comes back as a
sentence they can act on rather than a field error.

**The queue** — check in, call next, move a ticket along — is pressed
repeatedly by several people at once in different rooms, so the writes are
small, the reads are cheap, and the ordering decisions live in
``booking.py`` where they are locked rather than in the view.

Reading a clinic's diary is audited as a bulk read of patient data, because
that is what it is: a list of named people with the reason they are coming.
Individual bookings are audited as writes against the patient.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.models import Branch, Department
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.patients.models import Patient
from apps.scheduling import availability as avail
from apps.scheduling import booking
from apps.scheduling.models import (
    Appointment,
    Clinic,
    DoctorSchedule,
    QueueTicket,
    ScheduleException,
)

#: A cancellation reason is required and has to be a sentence. The patient
#: is going to be rung about this, and "x" tells the person ringing nothing.
MIN_REASON_LENGTH = 4


# ---------------------------------------------------------------------------
# Serialisers
# ---------------------------------------------------------------------------


class ClinicSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    service_name = serializers.CharField(source="service.name", read_only=True, default="")

    class Meta:
        model = Clinic
        fields = (
            "id",
            "name",
            "department",
            "department_name",
            "service_name",
            "default_duration_minutes",
            "booking_horizon_days",
            "accepts_walk_ins",
            "is_active",
        )


class ScheduleSerializer(serializers.ModelSerializer):
    staff_name = serializers.SerializerMethodField()
    places_per_session = serializers.IntegerField(read_only=True)

    class Meta:
        model = DoctorSchedule
        fields = (
            "id",
            "clinic",
            "staff",
            "staff_name",
            "weekday",
            "starts_at",
            "ends_at",
            "slot_minutes",
            "capacity_per_slot",
            "places_per_session",
            "valid_from",
            "valid_until",
            "is_active",
        )

    def get_staff_name(self, obj: DoctorSchedule) -> str:
        user = getattr(obj.staff, "user", None)
        return (user.get_full_name() or user.email) if user else ""


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    patient_mrn = serializers.CharField(source="patient.mrn", read_only=True)
    clinic_name = serializers.CharField(source="clinic.name", read_only=True)
    staff_name = serializers.SerializerMethodField()
    ticket_label = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = (
            "id",
            "patient",
            "patient_name",
            "patient_mrn",
            "clinic",
            "clinic_name",
            "staff",
            "staff_name",
            "starts_at",
            "duration_minutes",
            "status",
            "source",
            "kind",
            "reason",
            "checked_in_at",
            "cancelled_at",
            "cancellation_reason",
            "ticket_label",
        )

    def get_staff_name(self, obj: Appointment) -> str:
        user = getattr(obj.staff, "user", None)
        return (user.get_full_name() or user.email) if user else ""

    def get_ticket_label(self, obj: Appointment) -> str:
        ticket = getattr(obj, "ticket", None)
        return ticket.label if ticket else ""


class TicketSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    patient_mrn = serializers.CharField(source="patient.mrn", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    minutes_waiting = serializers.SerializerMethodField()

    class Meta:
        model = QueueTicket
        fields = (
            "id",
            "label",
            "number",
            "status",
            "priority",
            "patient",
            "patient_name",
            "patient_mrn",
            "department",
            "department_name",
            "issued_at",
            "called_at",
            "called_to",
            "minutes_waiting",
        )

    def get_minutes_waiting(self, obj: QueueTicket) -> int:
        return obj.minutes_waiting()


class BookSerializer(serializers.Serializer):
    patient_id = serializers.UUIDField()
    clinic_id = serializers.UUIDField()
    schedule_id = serializers.UUIDField()
    starts_at = serializers.DateTimeField()
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)
    # `.choices` rather than the enum class: DRF accepts either at runtime,
    # and the stubs type the argument as a sequence.
    #
    # The ignore is for the field's NAME, not its type: every DRF field
    # carries a `source` attribute, so a field called `source` shadows it in
    # the stubs. It works at runtime — Field.source is only read on a bound
    # field — and "source" is what this value is called in the API and in
    # the database, which is worth more than renaming it to suit a stub.
    source = serializers.ChoiceField(  # type: ignore[assignment]
        choices=Appointment.Source.choices,
        required=False,
        default=Appointment.Source.FRONT_DESK,
    )
    kind = serializers.ChoiceField(
        choices=Appointment.Kind.choices,
        required=False,
        default=Appointment.Kind.IN_PERSON,
    )


class RescheduleSerializer(serializers.Serializer):
    schedule_id = serializers.UUIDField()
    starts_at = serializers.DateTimeField()
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=MIN_REASON_LENGTH, max_length=200)


class CheckInSerializer(serializers.Serializer):
    patient_id = serializers.UUIDField()
    department_id = serializers.UUIDField(required=False, allow_null=True)
    appointment_id = serializers.UUIDField(required=False, allow_null=True)
    priority = serializers.IntegerField(required=False, default=0, min_value=-5, max_value=5)


class ExceptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScheduleException
        fields = ("id", "staff", "clinic", "kind", "starts_on", "ends_on", "reason")


# ---------------------------------------------------------------------------
# The diary
# ---------------------------------------------------------------------------


class ClinicListView(CuraviaAPIView):
    """Clinics at the active branch."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["appointment.view"]

    @extend_schema(responses={200: ClinicSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        clinics = Clinic.objects.filter(is_active=True).select_related("department", "service")
        return Response(ClinicSerializer(clinics, many=True).data)


class AvailabilityView(CuraviaAPIView):
    """
    What is free at a clinic over a window of days.

    Not audited: this says nothing about any patient. It is the one read in
    the module that carries no personal data at all, which is also why the
    patient portal can call it before anybody has signed in to anything.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["appointment.view"]
    throttle_scope = "search"

    @extend_schema(
        parameters=[
            OpenApiParameter("clinic", str, required=True),
            OpenApiParameter("from", str, required=False, description="YYYY-MM-DD"),
            OpenApiParameter("days", int, required=False),
            OpenApiParameter("staff", str, required=False),
            OpenApiParameter("include_full", bool, required=False),
        ],
        responses={200: OpenApiResponse(description="Slots per day")},
    )
    def get(self, request: CuraviaRequest) -> Response:
        clinic = Clinic.objects.filter(pk=request.query_params.get("clinic")).first()
        if clinic is None:
            return Response(
                {"detail": "No such clinic.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        start = parse_date(request.query_params.get("from", "") or "") or timezone.localdate()
        try:
            days = int(request.query_params.get("days", 7) or 7)
        except ValueError:
            days = 7

        window = avail.availability(
            clinic,
            start_day=start,
            days=days,
            staff_id=request.query_params.get("staff") or None,
            include_full=request.query_params.get("include_full") == "true",
        )
        return Response(
            {
                "clinic": ClinicSerializer(clinic).data,
                "days": [
                    {
                        "day": day.day,
                        "closed_because": day.closed_because,
                        "free_count": day.free_count,
                        "slots": [
                            {
                                "starts_at": slot.starts_at,
                                "minutes": slot.minutes,
                                "schedule_id": slot.schedule_id,
                                "staff_id": slot.staff_id,
                                "staff_name": slot.staff_name,
                                "capacity": slot.capacity,
                                "taken": slot.taken,
                                "free": slot.free,
                            }
                            for slot in day.slots
                        ],
                    }
                    for day in window
                ],
            }
        )


class AppointmentListView(CuraviaAPIView):
    """
    The diary: one day, or one patient's appointments.

    Audited when it returns a list of patients, flagged as a bulk read. A
    clinic list is a list of named people with the reason they are coming,
    and somebody exporting next Tuesday's clinic should leave a mark.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["appointment.view"]

    @extend_schema(
        parameters=[
            OpenApiParameter("day", str, required=False, description="YYYY-MM-DD"),
            OpenApiParameter("clinic", str, required=False),
            OpenApiParameter("patient", str, required=False),
            OpenApiParameter("status", str, required=False),
        ],
        responses={200: AppointmentSerializer(many=True)},
    )
    def get(self, request: CuraviaRequest) -> Response:
        rows = Appointment.objects.select_related(
            "patient", "clinic", "staff__user"
        ).prefetch_related("ticket")

        patient_id = request.query_params.get("patient")
        if patient_id:
            rows = rows.filter(patient_id=patient_id).order_by("-starts_at")[:100]
        else:
            day = parse_date(request.query_params.get("day", "") or "") or timezone.localdate()
            zone = timezone.get_current_timezone()
            start = timezone.make_aware(dt.datetime.combine(day, dt.time.min), zone)
            rows = rows.filter(
                starts_at__gte=start, starts_at__lt=start + dt.timedelta(days=1)
            ).order_by("starts_at")
            if clinic_id := request.query_params.get("clinic"):
                rows = rows.filter(clinic_id=clinic_id)

        if wanted := request.query_params.get("status"):
            rows = rows.filter(status=wanted)

        results = list(rows)
        if results and not patient_id:
            audit.record(
                "appointment.diary.view",
                actor=request.user,
                changes={
                    "day": request.query_params.get("day") or str(timezone.localdate()),
                    "appointments": len(results),
                },
                flags=[audit.AuditFlag.BULK],
                **as_audit_kwargs(),
            )
        return Response(AppointmentSerializer(results, many=True).data)


class BookView(CuraviaAPIView):
    """Book a patient into a slot."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["appointment.book"]

    @extend_schema(
        request=BookSerializer,
        responses={
            201: AppointmentSerializer,
            409: OpenApiResponse(description="That slot is not bookable"),
        },
    )
    def post(self, request: CuraviaRequest) -> Response:
        serializer = BookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        patient = Patient.objects.filter(pk=data["patient_id"]).first()
        clinic = Clinic.objects.filter(pk=data["clinic_id"]).first()
        schedule = (
            DoctorSchedule.objects.filter(pk=data["schedule_id"], clinic=clinic)
            .select_related("staff")
            .first()
        )
        if patient is None or clinic is None or schedule is None:
            return Response(
                {"detail": "The patient, clinic or session was not found.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            appointment = booking.book(
                booking.BookingRequest(
                    patient=patient,
                    clinic=clinic,
                    schedule=schedule,
                    starts_at=data["starts_at"],
                    reason=data.get("reason", ""),
                    source=data.get("source", Appointment.Source.FRONT_DESK),
                    kind=data.get("kind", Appointment.Kind.IN_PERSON),
                ),
                booked_by=request.user,
            )
        except booking.BookingError as exc:
            # 409 rather than 400: nothing the client sent was malformed —
            # the world changed, usually because somebody else took the slot.
            return Response(
                {"detail": str(exc), "code": "cannot_book"},
                status=status.HTTP_409_CONFLICT,
            )

        audit.record(
            "appointment.book",
            actor=request.user,
            object_type="Appointment",
            object_id=appointment.pk,
            patient_id=patient.pk,
            changes={
                "clinic": clinic.name,
                "starts_at": appointment.starts_at.isoformat(),
                "source": appointment.source,
            },
            **as_audit_kwargs(),
        )
        return Response(AppointmentSerializer(appointment).data, status=status.HTTP_201_CREATED)


class AppointmentActionView(CuraviaAPIView):
    """
    Move or cancel one appointment.

    Two verbs on one route because they are the same decision taken at the
    desk — "they cannot make Tuesday" — and splitting them would put the
    cancellation reason on one screen and the new time on another.
    """

    permission_classes = [IsAuthenticated, HasPermission]

    def get_permissions(self):  # type: ignore[no-untyped-def]
        # Cancelling is a narrower permission than moving: see
        # docs/permissions-matrix.yml. A clinician may move their own
        # follow-up; ringing a patient to say it is off belongs with the
        # people who will do the ringing.
        action = self.kwargs.get("action", "")
        self.required_permissions = (
            ["appointment.cancel"] if action == "cancel" else ["appointment.book"]
        )
        return super().get_permissions()

    @extend_schema(
        request=None,
        responses={200: AppointmentSerializer, 409: OpenApiResponse(description="Refused")},
        operation_id="appointments_move_or_cancel",
    )
    def post(self, request: CuraviaRequest, pk: str, action: str) -> Response:
        appointment = (
            Appointment.objects.filter(pk=pk)
            .select_related("patient", "clinic", "staff__user")
            .first()
        )
        if appointment is None:
            return Response(
                {"detail": "No such appointment.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if action == "cancel":
            cancelling = CancelSerializer(data=request.data)
            cancelling.is_valid(raise_exception=True)
            try:
                booking.cancel(
                    appointment,
                    reason=cancelling.validated_data["reason"],
                    cancelled_by=request.user,
                )
            except booking.BookingError as exc:
                return Response(
                    {"detail": str(exc), "code": "cannot_cancel"},
                    status=status.HTTP_409_CONFLICT,
                )
            audit.record(
                "appointment.cancel",
                actor=request.user,
                object_type="Appointment",
                object_id=appointment.pk,
                patient_id=appointment.patient_id,
                reason=cancelling.validated_data["reason"],
                **as_audit_kwargs(),
            )
            return Response(AppointmentSerializer(appointment).data)

        moving = RescheduleSerializer(data=request.data)
        moving.is_valid(raise_exception=True)
        schedule = DoctorSchedule.objects.filter(
            pk=moving.validated_data["schedule_id"], clinic=appointment.clinic
        ).first()
        if schedule is None:
            return Response(
                {"detail": "That session is not part of this clinic.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            moved = booking.reschedule(
                appointment,
                schedule=schedule,
                starts_at=moving.validated_data["starts_at"],
                reason=moving.validated_data.get("reason", ""),
                actor=request.user,
            )
        except booking.BookingError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_move"},
                status=status.HTTP_409_CONFLICT,
            )
        audit.record(
            "appointment.reschedule",
            actor=request.user,
            object_type="Appointment",
            object_id=moved.pk,
            patient_id=moved.patient_id,
            changes={
                "from": appointment.starts_at.isoformat(),
                "to": moved.starts_at.isoformat(),
            },
            **as_audit_kwargs(),
        )
        return Response(AppointmentSerializer(moved).data)


# ---------------------------------------------------------------------------
# Rotas and leave
# ---------------------------------------------------------------------------


class ScheduleListView(CuraviaAPIView):
    """The rota. Read with appointment.view, written with schedule.manage."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["appointment.view"]

    @extend_schema(
        parameters=[OpenApiParameter("clinic", str, required=False)],
        responses={200: ScheduleSerializer(many=True)},
    )
    def get(self, request: CuraviaRequest) -> Response:
        rows = DoctorSchedule.objects.filter(is_active=True).select_related("staff__user")
        if clinic_id := request.query_params.get("clinic"):
            rows = rows.filter(clinic_id=clinic_id)
        return Response(ScheduleSerializer(rows, many=True).data)


class ExceptionView(CuraviaAPIView):
    """
    Leave, holidays and closures.

    **Creating one never cancels an appointment.** The response carries the
    bookings it has just stranded so the screen can show them, and somebody
    decides what happens to each. See ``ScheduleException``.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["schedule.manage"]

    @extend_schema(
        parameters=[OpenApiParameter("preview", bool, required=False)],
        request=ExceptionSerializer,
        responses={201: OpenApiResponse(description="Created, with the appointments affected")},
    )
    def post(self, request: CuraviaRequest) -> Response:
        serializer = ExceptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        branch = _active_branch(request)
        if branch is None:
            return Response(
                {"detail": "Which branch is this for?", "code": "branch_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        exception = ScheduleException(
            organisation_id=request.organisation.pk,  # type: ignore[union-attr]
            branch=branch,
            created_by=request.user,
            **serializer.validated_data,
        )
        exception.full_clean(exclude=["organisation", "branch"])

        preview = request.query_params.get("preview") == "true"
        stranded = list(avail.affected_appointments(exception)[:200])
        payload = {
            "exception": ExceptionSerializer(exception).data,
            # Named, not counted. "12 appointments affected" is a number
            # somebody nods at; twelve names with phone numbers is a list
            # somebody works through.
            "affected": AppointmentSerializer(stranded, many=True).data,
            "affected_count": len(stranded),
        }
        if preview:
            return Response(payload)

        exception.save()
        payload["exception"] = ExceptionSerializer(exception).data
        audit.record(
            "schedule.exception.create",
            actor=request.user,
            object_type="ScheduleException",
            object_id=exception.pk,
            changes={
                "kind": exception.kind,
                "from": str(exception.starts_on),
                "to": str(exception.ends_on),
                "appointments_affected": len(stranded),
            },
            reason=exception.reason,
            **as_audit_kwargs(),
        )
        return Response(payload, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


class QueueView(CuraviaAPIView):
    """Today's queue at one department, or the whole branch."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["queue.manage"]

    @extend_schema(
        parameters=[OpenApiParameter("department", str, required=False)],
        responses={200: TicketSerializer(many=True)},
    )
    def get(self, request: CuraviaRequest) -> Response:
        rows = QueueTicket.objects.filter(
            day=timezone.localdate(), status__in=QueueTicket.ACTIVE_STATUSES
        ).select_related("patient", "department")
        if department_id := request.query_params.get("department"):
            rows = rows.filter(department_id=department_id)
        return Response(TicketSerializer(rows.order_by("priority", "issued_at"), many=True).data)


class CheckInView(CuraviaAPIView):
    """Put somebody in the queue and hand them a number."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["queue.manage"]

    @extend_schema(request=CheckInSerializer, responses={201: TicketSerializer})
    def post(self, request: CuraviaRequest) -> Response:
        serializer = CheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        patient = Patient.objects.filter(pk=data["patient_id"]).first()
        if patient is None:
            return Response(
                {"detail": "No such patient.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        branch = _active_branch(request)
        if branch is None:
            return Response(
                {"detail": "Which branch is this?", "code": "branch_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        appointment = None
        if data.get("appointment_id"):
            appointment = Appointment.objects.filter(pk=data["appointment_id"]).first()
            if appointment is None:
                return Response(
                    {"detail": "No such appointment.", "code": "not_found"},
                    status=status.HTTP_404_NOT_FOUND,
                )

        department = _department_for(request, data.get("department_id"), appointment)
        if department is None:
            return Response(
                {
                    "detail": "Which department are they being seen in?",
                    "code": "department_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            ticket = booking.check_in(
                patient=patient,
                department=department,
                branch=branch,
                appointment=appointment,
                priority=data.get("priority", 0),
            )
        except booking.BookingError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_check_in"},
                status=status.HTTP_409_CONFLICT,
            )

        audit.record(
            "queue.check_in",
            actor=request.user,
            object_type="QueueTicket",
            object_id=ticket.pk,
            patient_id=patient.pk,
            changes={"token": ticket.label, "department": department.name},
            **as_audit_kwargs(),
        )
        return Response(TicketSerializer(ticket).data, status=status.HTTP_201_CREATED)


class CallNextView(CuraviaAPIView):
    """Call the next patient in a department's queue."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["queue.manage"]

    @extend_schema(request=None, responses={200: TicketSerializer})
    def post(self, request: CuraviaRequest) -> Response:
        body: Any = request.data if hasattr(request.data, "get") else {}
        department = Department.objects.filter(pk=body.get("department_id")).first()
        branch = _active_branch(request)
        if department is None or branch is None:
            return Response(
                {"detail": "Which department?", "code": "department_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket = booking.call_next(
            department=department, branch=branch, called_to=str(body.get("called_to", ""))
        )
        if ticket is None:
            return Response({"detail": "Nobody is waiting.", "code": "queue_empty"})
        return Response(TicketSerializer(ticket).data)


class TicketActionView(CuraviaAPIView):
    """Move one ticket along the queue."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["queue.manage"]

    @extend_schema(
        request=None,
        responses={200: TicketSerializer},
        operation_id="queue_ticket_set_status",
    )
    def post(self, request: CuraviaRequest, pk: str, action: str) -> Response:
        allowed = {
            "serving": QueueTicket.Status.SERVING,
            "done": QueueTicket.Status.DONE,
            "skipped": QueueTicket.Status.SKIPPED,
            "left": QueueTicket.Status.LEFT,
        }
        if action not in allowed:
            return Response(
                {"detail": f"There is nothing called {action}.", "code": "unknown_action"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ticket = QueueTicket.objects.filter(pk=pk).select_related("patient", "department").first()
        if ticket is None:
            return Response(
                {"detail": "No such ticket.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        booking.set_ticket_status(ticket, allowed[action])
        return Response(TicketSerializer(ticket).data)


# ---------------------------------------------------------------------------


def _active_branch(request: CuraviaRequest) -> Branch | None:
    from apps.core.tenancy import current_branch_id

    active = current_branch_id()
    if active:
        return Branch.objects.filter(pk=active).first()
    return Branch.objects.filter(is_main=True).first()


def _department_for(
    request: CuraviaRequest, department_id, appointment: Appointment | None
) -> Department | None:
    """
    Which queue to join: the one asked for, or the clinic's own.

    A booked patient should not have to be told which department they are
    joining — their appointment already says.
    """
    if department_id:
        return Department.objects.filter(pk=department_id).first()
    if appointment is not None:
        return Department.objects.filter(pk=appointment.clinic.department_id).first()
    return None


__all__ = [
    "AppointmentActionView",
    "AppointmentListView",
    "AvailabilityView",
    "BookView",
    "CallNextView",
    "CheckInView",
    "ClinicListView",
    "ExceptionView",
    "QueueView",
    "ScheduleListView",
    "TicketActionView",
]
