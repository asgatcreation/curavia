"""
Triage over HTTP.

Observations are written with ``triage.vitals.record`` and read with
``clinical.note.read``, which is not an oversight: taking a pulse and reading
somebody's chart are different acts. A healthcare assistant may be trusted to
record a temperature without being trusted to read the history it goes into.

Every read of a chart is audited, because a chart is the clinical record.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.clinical import news2, triage
from apps.clinical.models import NursingNote, Observation, TriageAssessment, TriageCategory
from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.models import Branch
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.journey.models import Encounter


class ObservationSerializer(serializers.ModelSerializer):
    taken_by_name = serializers.SerializerMethodField()
    blood_pressure = serializers.CharField(read_only=True)
    is_current = serializers.BooleanField(read_only=True)
    bmi = serializers.SerializerMethodField()

    class Meta:
        model = Observation
        fields = (
            "id",
            "taken_at",
            "taken_by_name",
            "respiration_rate",
            "spo2",
            "on_oxygen",
            "oxygen_litres",
            "systolic_bp",
            "diastolic_bp",
            "blood_pressure",
            "pulse",
            "temperature",
            "consciousness",
            "weight_kg",
            "height_cm",
            "blood_glucose",
            "pain_score",
            "news2_score",
            "news2_red_flag",
            "news2_band",
            "score_version",
            "oxygen_scale",
            "note",
            "is_current",
            "correction_reason",
            "bmi",
        )

    def get_taken_by_name(self, obj: Observation) -> str:
        user = obj.taken_by
        return (user.get_full_name() or user.email) if user else ""

    def get_bmi(self, obj: Observation) -> dict[str, Any] | None:
        if not (obj.weight_kg and obj.height_cm):
            return None
        value = news2.bmi(
            weight_kg=float(obj.weight_kg),
            height_cm=float(obj.height_cm),
            age_years=triage.patient_age(obj.patient),
        )
        if value is None:  # pragma: no cover - guarded above
            return None
        return {"value": value.value, "category": value.category, "caveat": value.caveat}


class TriageSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    patient_mrn = serializers.CharField(source="patient.mrn", read_only=True)
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    target_minutes = serializers.IntegerField(read_only=True)

    class Meta:
        model = TriageAssessment
        fields = (
            "id",
            "encounter",
            "patient_name",
            "patient_mrn",
            "category",
            "category_label",
            "complaint",
            "assessed_at",
            "target_minutes",
            "revision_reason",
            "revised_at",
        )


class NoteSerializer(serializers.ModelSerializer):
    written_by_name = serializers.SerializerMethodField()
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = NursingNote
        fields = (
            "id",
            "kind",
            "kind_label",
            "body",
            "written_at",
            "written_by_name",
            "amends",
        )

    def get_written_by_name(self, obj: NursingNote) -> str:
        user = obj.written_by
        return (user.get_full_name() or user.email) if user else ""


class ObservationInputSerializer(serializers.Serializer):
    """
    Every field optional, deliberately.

    A triage nurse with forty people waiting takes a pulse and a temperature
    and moves on. A form that refuses to save without a respiratory rate
    produces an invented respiratory rate, which is worse than a blank
    because it gets scored.
    """

    respiration_rate = serializers.IntegerField(required=False, allow_null=True)
    spo2 = serializers.IntegerField(required=False, allow_null=True)
    on_oxygen = serializers.BooleanField(required=False, allow_null=True)
    oxygen_litres = serializers.DecimalField(
        max_digits=4, decimal_places=1, required=False, allow_null=True
    )
    systolic_bp = serializers.IntegerField(required=False, allow_null=True)
    diastolic_bp = serializers.IntegerField(required=False, allow_null=True)
    pulse = serializers.IntegerField(required=False, allow_null=True)
    temperature = serializers.DecimalField(
        max_digits=4, decimal_places=1, required=False, allow_null=True
    )
    consciousness = serializers.CharField(max_length=1, required=False, allow_blank=True)
    weight_kg = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )
    height_cm = serializers.DecimalField(
        max_digits=5, decimal_places=1, required=False, allow_null=True
    )
    blood_glucose = serializers.DecimalField(
        max_digits=5, decimal_places=1, required=False, allow_null=True
    )
    pain_score = serializers.IntegerField(required=False, allow_null=True)
    position = serializers.CharField(max_length=10, required=False, allow_blank=True)
    oxygen_scale = serializers.IntegerField(required=False, default=1, min_value=1, max_value=2)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True)
    supersedes_id = serializers.UUIDField(required=False, allow_null=True)
    correction_reason = serializers.CharField(max_length=200, required=False, allow_blank=True)


class AssessInputSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=TriageCategory.choices)
    complaint = serializers.CharField(max_length=200)
    observation_id = serializers.UUIDField(required=False, allow_null=True)


class NoteInputSerializer(serializers.Serializer):
    body = serializers.CharField()
    kind = serializers.ChoiceField(
        choices=NursingNote.Kind.choices, required=False, default=NursingNote.Kind.OBSERVATION
    )
    amends_id = serializers.UUIDField(required=False, allow_null=True)


def _score_payload(computed: news2.Score) -> dict[str, Any]:
    """
    The score, with its working shown.

    The parameters are returned individually because a nurse looking at a 7
    needs to know which reading produced it — and because a single parameter
    scoring 3 escalates on its own, which a total alone cannot express.
    """
    return {
        "total": computed.total,
        "band": computed.band,
        "response": computed.response,
        "red_flag": computed.red_flag,
        "usable": computed.usable,
        "reason": computed.reason,
        "missing": list(computed.missing),
        "is_complete": computed.is_complete,
        "parameters": [
            {
                "name": parameter.name,
                "label": parameter.label,
                "value": parameter.value,
                "score": parameter.score,
            }
            for parameter in computed.parameters
        ],
    }


class WaitingRoomView(CuraviaAPIView):
    """Everybody triaged and still waiting, most urgent first."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["triage.vitals.record"]

    @extend_schema(responses={200: OpenApiResponse(description="The triage queue")})
    def get(self, request: CuraviaRequest) -> Response:
        branch = _active_branch(request)
        if branch is None:
            return Response(
                {"detail": "Which branch?", "code": "branch_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = triage.waiting_room(branch.pk)
        if rows:
            audit.record(
                "triage.queue.view",
                actor=request.user,
                changes={"patients": len(rows)},
                flags=[audit.AuditFlag.BULK],
                **as_audit_kwargs(),
            )
        return Response({"rows": rows})


class ObservationView(CuraviaAPIView):
    """Record a set of vitals against one visit."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["triage.vitals.record"]

    @extend_schema(
        request=ObservationInputSerializer,
        responses={201: OpenApiResponse(description="Stored, with the score")},
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = ObservationInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        encounter = Encounter.objects.filter(pk=pk).select_related("patient", "ticket").first()
        if encounter is None:
            return Response(
                {"detail": "No such visit.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        supersedes = None
        if supersedes_id := data.pop("supersedes_id", None):
            supersedes = Observation.objects.filter(pk=supersedes_id).first()
            if supersedes is None:
                return Response(
                    {"detail": "That earlier reading was not found.", "code": "not_found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
        correction_reason = data.pop("correction_reason", "")

        try:
            observation, computed = triage.record_observation(
                encounter=encounter,
                data=data,
                actor=request.user,
                supersedes=supersedes,
                correction_reason=correction_reason,
            )
        except triage.TriageError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_record"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError

            if isinstance(exc, DjangoValidationError):
                return Response(
                    {
                        "detail": "Some of those readings need checking.",
                        "code": "invalid",
                        "fields": getattr(exc, "message_dict", {"detail": exc.messages}),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise

        audit.record(
            "triage.vitals.record",
            actor=request.user,
            object_type="Observation",
            object_id=observation.pk,
            patient_id=encounter.patient_id,
            changes={
                "news2": observation.news2_score,
                "band": observation.news2_band,
                "corrected": bool(supersedes),
            },
            flags=[audit.AuditFlag.SENSITIVE] if computed.red_flag else [],
            **as_audit_kwargs(),
        )
        return Response(
            {
                "observation": ObservationSerializer(observation).data,
                "score": _score_payload(computed),
            },
            status=status.HTTP_201_CREATED,
        )


class ChartView(CuraviaAPIView):
    """One patient's observation chart."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.read"]

    @extend_schema(
        parameters=[OpenApiParameter("limit", int, required=False)],
        responses={200: ObservationSerializer(many=True)},
    )
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        from apps.patients.models import Patient

        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return Response(
                {"detail": "No such patient.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        rows = triage.chart(patient)
        audit.record_view(
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            fields=["observations"],
            **as_audit_kwargs(),
        )
        return Response(ObservationSerializer(rows, many=True).data)


class AssessView(CuraviaAPIView):
    """Record or revise how urgent somebody is."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["triage.vitals.record"]

    @extend_schema(request=AssessInputSerializer, responses={200: TriageSerializer})
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = AssessInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        encounter = Encounter.objects.filter(pk=pk).select_related("patient", "ticket").first()
        if encounter is None:
            return Response(
                {"detail": "No such visit.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        observation = (
            Observation.objects.filter(pk=data["observation_id"]).first()
            if data.get("observation_id")
            else triage.latest(encounter)
        )

        try:
            assessment = triage.assess(
                encounter=encounter,
                category=data["category"],
                complaint=data["complaint"],
                actor=request.user,
                observation=observation,
            )
        except triage.TriageError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_assess"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit.record(
            "triage.assess",
            actor=request.user,
            object_type="TriageAssessment",
            object_id=assessment.pk,
            patient_id=encounter.patient_id,
            changes={"category": assessment.category},
            **as_audit_kwargs(),
        )
        return Response(TriageSerializer(assessment).data)


class NotesView(CuraviaAPIView):
    """Nursing notes for one visit."""

    permission_classes = [IsAuthenticated, HasPermission]

    def get_permissions(self):  # type: ignore[no-untyped-def]
        # Reading a note and writing one are different permissions, and this
        # view does both.
        self.required_permissions = (
            ["triage.note.write"] if self.request.method == "POST" else ["clinical.note.read"]
        )
        return super().get_permissions()

    @extend_schema(responses={200: NoteSerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        encounter = Encounter.objects.filter(pk=pk).first()
        if encounter is None:
            return Response(
                {"detail": "No such visit.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        rows = (
            NursingNote.objects.filter(encounter=encounter)
            .select_related("written_by")
            .order_by("written_at")
        )
        audit.record_view(
            actor=request.user,
            object_type="Encounter",
            object_id=encounter.pk,
            patient_id=encounter.patient_id,
            fields=["nursing_notes"],
            **as_audit_kwargs(),
        )
        return Response(NoteSerializer(rows, many=True).data)

    @extend_schema(
        request=NoteInputSerializer,
        responses={201: NoteSerializer},
        operation_id="clinical_write_nursing_note",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = NoteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return Response(
                {"detail": "No such visit.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        amends = (
            NursingNote.objects.filter(pk=data["amends_id"]).first()
            if data.get("amends_id")
            else None
        )

        try:
            note = triage.write_note(
                encounter=encounter,
                body=data["body"],
                kind=data.get("kind", NursingNote.Kind.OBSERVATION),
                actor=request.user,
                amends=amends,
            )
        except triage.TriageError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_write"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError

            if isinstance(exc, DjangoValidationError):
                return Response(
                    {"detail": "That note needs something in it.", "code": "invalid"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise

        audit.record(
            "clinical.note.write",
            actor=request.user,
            object_type="NursingNote",
            object_id=note.pk,
            patient_id=encounter.patient_id,
            changes={"kind": note.kind, "is_addendum": bool(amends)},
            **as_audit_kwargs(),
        )
        return Response(NoteSerializer(note).data, status=status.HTTP_201_CREATED)


class ScorePreviewView(CuraviaAPIView):
    """
    Score a set of readings without storing them.

    The triage form calls this as the nurse types, so the score appears
    beside the numbers rather than after a save. It writes nothing, which is
    why it is a separate route and not a flag on the one that does.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["triage.vitals.record"]
    throttle_scope = "search"

    @extend_schema(
        request=ObservationInputSerializer,
        responses={200: OpenApiResponse(description="The score, with its working")},
    )
    def post(self, request: CuraviaRequest) -> Response:
        serializer = ObservationInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        age = None
        # `request.data` is a list for a JSON array body, which a client
        # posting the wrong shape would send.
        body: Any = request.data if hasattr(request.data, "get") else {}
        if patient_id := body.get("patient_id"):
            from apps.patients.models import Patient

            patient = Patient.objects.filter(pk=patient_id).first()
            if patient is not None:
                age = triage.patient_age(patient)

        computed = news2.score(
            respiration_rate=data.get("respiration_rate"),
            spo2=data.get("spo2"),
            on_oxygen=data.get("on_oxygen"),
            systolic_bp=data.get("systolic_bp"),
            pulse=data.get("pulse"),
            consciousness=data.get("consciousness") or None,
            temperature=float(data["temperature"]) if data.get("temperature") is not None else None,
            scale=news2.OxygenScale(data.get("oxygen_scale") or 1),
            age_years=age,
        )
        return Response(_score_payload(computed))


def _active_branch(request: CuraviaRequest) -> Branch | None:
    from apps.core.tenancy import current_branch_id

    active = current_branch_id()
    if active:
        return Branch.objects.filter(pk=active).first()
    return Branch.objects.filter(is_main=True).first()


__all__ = [
    "AssessView",
    "ChartView",
    "NotesView",
    "ObservationView",
    "ScorePreviewView",
    "WaitingRoomView",
]
