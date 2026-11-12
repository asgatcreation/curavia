"""
The ward's list and its handover, over HTTP.

Reading and working are separate permissions on purpose. A doctor on a ward
round needs to see whether the antibiotic has been given — "has she had it
yet" is a ward-round question, and a doctor who cannot see the answer asks
the nurse, which costs her the time the list was meant to save. Completing a
task is different: it puts a nurse's name against something done to a
patient, so only nurses do it.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.models import Department
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.journey.models import Encounter
from apps.nursing import service as nursing
from apps.nursing.models import Handover, NursingTask, TaskKind, TaskStatus
from apps.patients.models import Patient


class NursingTaskSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    patient_name = serializers.SerializerMethodField()
    is_overdue = serializers.BooleanField(read_only=True)
    minutes_late = serializers.IntegerField(read_only=True)

    class Meta:
        model = NursingTask
        fields = (
            "id",
            "patient",
            "patient_name",
            "encounter",
            "department",
            "kind",
            "kind_label",
            "name",
            "detail",
            "order",
            "due_at",
            "urgent",
            "status",
            "status_label",
            "done_at",
            "done_by_name",
            "done_note",
            "not_done_reason",
            "handovers",
            "is_overdue",
            "minutes_late",
            "created_at",
        )
        read_only_fields = fields

    def get_patient_name(self, obj: NursingTask) -> str:
        return obj.patient.full_name


class HandoverSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    signature = serializers.SerializerMethodField()
    #: True when the person reading it is the one who gave it. The screen
    #: uses this to stop offering "I have taken the ward" to the nurse going
    #: off — the server refuses it anyway, and an offer that is always
    #: refused is worse than no offer.
    from_me = serializers.SerializerMethodField()
    patient_count = serializers.IntegerField(read_only=True)
    outstanding_count = serializers.IntegerField(read_only=True)
    is_signed = serializers.BooleanField(read_only=True)

    class Meta:
        model = Handover
        fields = (
            "id",
            "department",
            "shift",
            "from_nurse_name",
            "from_nurse_role",
            "to_nurse_name",
            "status",
            "status_label",
            "content",
            "outstanding",
            "signed_at",
            "accepted_at",
            "accepted_note",
            "signature",
            "from_me",
            "patient_count",
            "outstanding_count",
            "is_signed",
            "created_at",
        )
        read_only_fields = fields

    def get_signature(self, obj: Handover) -> list[str]:
        return obj.signature_block()

    def get_from_me(self, obj: Handover) -> bool:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return bool(user and obj.from_nurse_id == user.pk)


# ---------------------------------------------------------------------------
# What a client may send
# ---------------------------------------------------------------------------


class TaskInputSerializer(serializers.Serializer):
    patient_id = serializers.UUIDField()
    name = serializers.CharField(max_length=200)
    encounter_id = serializers.UUIDField(required=False, allow_null=True)
    department_id = serializers.UUIDField(required=False, allow_null=True)
    kind = serializers.ChoiceField(choices=TaskKind.choices, required=False, default=TaskKind.CARE)
    detail = serializers.CharField(required=False, allow_blank=True, default="")
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    urgent = serializers.BooleanField(required=False, default=False)


class TaskCloseSerializer(serializers.Serializer):
    """
    How a task is closed.

    ``action`` rather than a status field, because "done" and "not done" are
    not two values of one thing — they lead to different next steps, and
    only one of them needs a reason.
    """

    action = serializers.ChoiceField(choices=["done", "not_done", "cancelled"])
    note = serializers.CharField(required=False, allow_blank=True, default="")
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class HandoverContentSerializer(serializers.Serializer):
    content = serializers.DictField(required=False)
    shift = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")


class AcceptSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, default="")


class WardListSerializer(serializers.Serializer):
    """The ward screen's one call, so the schema describes it."""

    tasks = NursingTaskSerializer(many=True, read_only=True)
    handover = HandoverSerializer(read_only=True)
    draft = HandoverSerializer(read_only=True)


def _not_found(what: str) -> Response:
    return Response({"detail": what, "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)


def _refused(exc: Exception, code: str = "refused") -> Response:
    return Response({"detail": str(exc), "code": code}, status=status.HTTP_400_BAD_REQUEST)


def _forbidden(detail: str) -> Response:
    return Response({"detail": detail, "code": "forbidden"}, status=status.HTTP_403_FORBIDDEN)


def _holds(request: CuraviaRequest, code: str) -> bool:
    """A second code on a route whose read and write differ."""
    held: frozenset[str] = getattr(request, "tenant_permissions", frozenset())
    return code in held


def _body(request: CuraviaRequest) -> Any:
    return request.data if hasattr(request.data, "get") else {}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


#: Where nursing work happens. A nurse's ward picker listing the pharmacy,
#: the store and the records room is a picker she has to read past ten times
#: a shift to reach the two places she actually works.
NURSED_KINDS = (
    Department.Kind.WARD,
    Department.Kind.CLINIC,
    Department.Kind.THEATRE,
)


class WardSerializer(serializers.ModelSerializer):
    """
    A ward or clinic a nurse can work on, with how much is waiting.

    Its own route rather than reusing /departments/, which needs
    org.settings.view — a permission a nurse has no business holding. A
    nurse who cannot list her own wards cannot open her own list.
    """

    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    open_tasks = serializers.IntegerField(read_only=True)
    overdue_tasks = serializers.IntegerField(read_only=True)

    class Meta:
        model = Department
        fields = ("id", "name", "code", "kind", "kind_label", "open_tasks", "overdue_tasks")
        read_only_fields = fields


class WardsView(CuraviaAPIView):
    """The wards and clinics this nurse can open."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.task.view"]

    @extend_schema(responses={200: WardSerializer(many=True)}, operation_id="wards_list")
    def get(self, request: CuraviaRequest) -> Response:
        now = timezone.now()
        # in_current_branch, like every other department listing: the default
        # manager scopes to the hospital, not to the branch, so without it a
        # nurse at Ikeja is offered Lekki's wards.
        rows = (
            Department.objects.filter(is_active=True, kind__in=NURSED_KINDS)
            .in_current_branch()
            .annotate(
                open_tasks=Count(
                    "nursing_tasks",
                    filter=Q(nursing_tasks__status=TaskStatus.PENDING),
                    distinct=True,
                ),
                overdue_tasks=Count(
                    "nursing_tasks",
                    filter=Q(
                        nursing_tasks__status=TaskStatus.PENDING,
                        nursing_tasks__due_at__lt=now,
                    ),
                    distinct=True,
                ),
            )
            .order_by("-open_tasks", "name")
        )
        return Response({"wards": WardSerializer(rows, many=True).data})


class WardTasksView(CuraviaAPIView):
    """
    One ward's list, and the handover waiting to be read.

    One call rather than three, because this is the screen a nurse opens at
    the start of a shift and three spinners in one column read as three
    things being broken.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.task.view"]

    @extend_schema(responses={200: WardListSerializer}, operation_id="ward_tasks")
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        department = Department.objects.filter(pk=pk).first()
        if department is None:
            return _not_found("No such ward or clinic.")

        tasks = nursing.open_tasks(department=department)
        handover = nursing.latest_received(department)
        draft = (
            Handover.objects.filter(
                department=department, from_nurse=request.user, status="draft"
            ).first()
            if _holds(request, "nursing.handover.write")
            else None
        )

        audit.record_view(
            actor=request.user,
            object_type="NursingTask",
            object_id=department.pk,
            **as_audit_kwargs(),
        )
        return Response(
            {
                "tasks": NursingTaskSerializer(tasks, many=True).data,
                "handover": HandoverSerializer(handover, context={"request": request}).data
                if handover
                else None,
                "draft": HandoverSerializer(draft, context={"request": request}).data
                if draft
                else None,
            }
        )


class TaskListView(CuraviaAPIView):
    """Add one by hand."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.task.manage"]

    @extend_schema(
        request=TaskInputSerializer,
        responses={201: NursingTaskSerializer},
        operation_id="nursing_task_add",
    )
    def post(self, request: CuraviaRequest) -> Response:
        body = _body(request)
        patient = Patient.objects.filter(pk=body.get("patient_id")).first()
        if patient is None:
            return _not_found("No such patient.")

        encounter = None
        if body.get("encounter_id"):
            encounter = Encounter.objects.filter(pk=body["encounter_id"]).first()
            if encounter is None:
                return _not_found("No such visit.")

        department = None
        if body.get("department_id"):
            department = Department.objects.filter(pk=body["department_id"]).first()

        try:
            task = nursing.add_task(
                patient=patient,
                encounter=encounter,
                department=department,
                name=str(body.get("name", "")),
                kind=str(body.get("kind", TaskKind.CARE)),
                detail=str(body.get("detail", "")),
                due_at=body.get("due_at") or None,
                urgent=bool(body.get("urgent", False)),
                actor=request.user,
            )
        except nursing.NursingError as exc:
            return _refused(exc, code="cannot_add_task")

        audit.record(
            "nursing.task.add",
            actor=request.user,
            object_type="NursingTask",
            object_id=task.pk,
            patient_id=patient.pk,
            changes={"name": task.name, "kind": task.kind},
            **as_audit_kwargs(),
        )
        return Response(NursingTaskSerializer(task).data, status=status.HTTP_201_CREATED)


class TaskDetailView(CuraviaAPIView):
    """Close one: done, not done, or cancelled."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.task.manage"]

    @extend_schema(
        request=TaskCloseSerializer,
        responses={200: NursingTaskSerializer},
        operation_id="nursing_task_close",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        task = NursingTask.objects.filter(pk=pk).select_related("patient").first()
        if task is None:
            return _not_found("No such task.")

        body = _body(request)
        action = str(body.get("action", ""))
        reason = str(body.get("reason", ""))
        try:
            if action == "done":
                task = nursing.complete(
                    task=task, actor=request.user, note=str(body.get("note", ""))
                )
            elif action == "not_done":
                task = nursing.mark_not_done(task=task, reason=reason, actor=request.user)
            elif action == "cancelled":
                task = nursing.cancel(task=task, reason=reason, actor=request.user)
            else:
                return Response(
                    {"detail": f"There is no action called {action!r}.", "code": "bad_action"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except nursing.NursingError as exc:
            return _refused(exc, code="cannot_close_task")

        audit.record(
            f"nursing.task.{action}",
            actor=request.user,
            object_type="NursingTask",
            object_id=task.pk,
            patient_id=task.patient_id,
            changes={"name": task.name, "reason": task.not_done_reason},
            **as_audit_kwargs(),
        )
        return Response(NursingTaskSerializer(task).data)


class EncounterTasksView(CuraviaAPIView):
    """This visit's list, rebuilt from its orders and observations."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.task.view"]

    @extend_schema(
        responses={200: NursingTaskSerializer(many=True)}, operation_id="encounter_tasks"
    )
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return _not_found("No such visit.")

        # Rebuilt on read. It is idempotent, and a list that only refreshes
        # when somebody presses a button is a list that is quietly stale at
        # the moment it matters.
        if _holds(request, "nursing.task.manage"):
            nursing.refresh(encounter)

        rows = NursingTask.objects.filter(encounter=encounter).select_related("patient", "order")
        audit.record_view(
            actor=request.user,
            object_type="NursingTask",
            object_id=encounter.pk,
            patient_id=encounter.patient_id,
            **as_audit_kwargs(),
        )
        return Response({"tasks": NursingTaskSerializer(rows, many=True).data})


class PatientTasksView(CuraviaAPIView):
    """Everything ever asked of a nurse for this patient."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.task.view"]

    @extend_schema(responses={200: NursingTaskSerializer(many=True)}, operation_id="patient_tasks")
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return _not_found("No such patient.")

        rows = nursing.tasks_for_patient(patient)
        audit.record_view(
            actor=request.user,
            object_type="NursingTask",
            object_id=patient.pk,
            patient_id=patient.pk,
            **as_audit_kwargs(),
        )
        return Response({"tasks": NursingTaskSerializer(rows, many=True).data})


# ---------------------------------------------------------------------------
# Handover
# ---------------------------------------------------------------------------


class HandoverListView(CuraviaAPIView):
    """This ward's handovers, and starting one."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.handover.read"]

    @extend_schema(responses={200: HandoverSerializer(many=True)}, operation_id="handovers_list")
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        department = Department.objects.filter(pk=pk).first()
        if department is None:
            return _not_found("No such ward or clinic.")

        rows = nursing.handovers_for(department)
        audit.record_view(
            actor=request.user,
            object_type="Handover",
            object_id=department.pk,
            **as_audit_kwargs(),
        )
        return Response(
            {"handovers": HandoverSerializer(rows, many=True, context={"request": request}).data}
        )

    @extend_schema(
        request=HandoverContentSerializer,
        responses={201: HandoverSerializer},
        operation_id="handovers_open",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        if not _holds(request, "nursing.handover.write"):
            return _forbidden("Your role cannot write a handover.")
        department = Department.objects.filter(pk=pk).first()
        if department is None:
            return _not_found("No such ward or clinic.")

        handover = nursing.open_handover(
            department=department,
            actor=request.user,
            shift=str(_body(request).get("shift", "")),
        )
        return Response(
            HandoverSerializer(handover, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class HandoverDetailView(CuraviaAPIView):
    """Read one, or save the draft."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.handover.read"]

    @extend_schema(responses={200: HandoverSerializer}, operation_id="handovers_get")
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        handover = Handover.objects.filter(pk=pk).first()
        if handover is None:
            return _not_found("No such handover.")

        audit.record_view(
            actor=request.user,
            object_type="Handover",
            object_id=handover.pk,
            **as_audit_kwargs(),
        )
        return Response(HandoverSerializer(handover, context={"request": request}).data)

    @extend_schema(
        request=HandoverContentSerializer,
        responses={200: HandoverSerializer},
        operation_id="handovers_save",
    )
    def patch(self, request: CuraviaRequest, pk: str) -> Response:
        if not _holds(request, "nursing.handover.write"):
            return _forbidden("Your role cannot write a handover.")
        handover = Handover.objects.filter(pk=pk).first()
        if handover is None:
            return _not_found("No such handover.")
        if handover.from_nurse_id != request.user.pk:
            return _forbidden("That handover is being written by somebody else.")

        content = _body(request).get("content")
        try:
            handover = nursing.save_draft(
                handover=handover,
                content=content if isinstance(content, dict) else {},
                actor=request.user,
            )
        except nursing.NursingError as exc:
            return _refused(exc, code="cannot_save")

        return Response(HandoverSerializer(handover, context={"request": request}).data)


class SignHandoverView(CuraviaAPIView):
    """
    Hand it to the next shift.

    Its own route: writing is thinking, and signing is the moment the ward
    becomes somebody else's responsibility. The outstanding list is built
    here, from the database, never from what the client sent.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.handover.write"]

    @extend_schema(
        request=HandoverContentSerializer,
        responses={200: HandoverSerializer},
        operation_id="handovers_sign",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        handover = Handover.objects.filter(pk=pk).first()
        if handover is None:
            return _not_found("No such handover.")

        try:
            handover = nursing.sign(
                handover=handover,
                actor=request.user,
                membership=getattr(request, "membership", None),
                shift=str(_body(request).get("shift", "")),
            )
        except nursing.NursingError as exc:
            return _refused(exc, code="cannot_sign")

        audit.record(
            "nursing.handover.sign",
            actor=request.user,
            object_type="Handover",
            object_id=handover.pk,
            changes={
                "ward": str(handover.department_id),
                "outstanding": handover.outstanding_count,
            },
            **as_audit_kwargs(),
        )
        return Response(HandoverSerializer(handover, context={"request": request}).data)


class AcceptHandoverView(CuraviaAPIView):
    """
    Take it.

    A handover nobody received is a handover that did not happen, and the
    ward has to be able to tell that apart from one that was never written.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["nursing.handover.write"]

    @extend_schema(
        request=AcceptSerializer,
        responses={200: HandoverSerializer},
        operation_id="handovers_accept",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        handover = Handover.objects.filter(pk=pk).first()
        if handover is None:
            return _not_found("No such handover.")

        try:
            handover = nursing.accept(
                handover=handover,
                actor=request.user,
                note=str(_body(request).get("note", "")),
            )
        except nursing.NursingError as exc:
            return _refused(exc, code="cannot_accept")

        audit.record(
            "nursing.handover.accept",
            actor=request.user,
            object_type="Handover",
            object_id=handover.pk,
            changes={"ward": str(handover.department_id)},
            **as_audit_kwargs(),
        )
        return Response(HandoverSerializer(handover, context={"request": request}).data)


__all__ = [
    "AcceptHandoverView",
    "EncounterTasksView",
    "HandoverDetailView",
    "HandoverListView",
    "PatientTasksView",
    "SignHandoverView",
    "TaskDetailView",
    "TaskListView",
    "WardTasksView",
    "WardsView",
]
