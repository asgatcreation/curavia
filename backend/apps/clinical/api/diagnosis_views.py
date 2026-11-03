"""
Diagnoses and the problem list over HTTP.

Gated on ``clinical.note.write`` and ``clinical.note.read`` rather than a
pair of permission codes of their own. Coding a visit is part of writing the
consultation, by the same person in the same minute, and a hospital that
could let somebody write a note but not say what was wrong with the patient
would be configuring something nobody wants. The permissions matrix stays
the size it earns.

The search endpoint is the exception that proves it: it reads a published
classification of disease and nothing about any patient, so it is behind
``clinical.note.read`` and throttled like a search rather than audited like
a record.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.clinical import diagnoses, icd10
from apps.clinical.models import Certainty, Diagnosis, Problem
from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.journey.models import Encounter


class DiagnosisSerializer(serializers.ModelSerializer):
    diagnosed_by_name = serializers.SerializerMethodField()
    certainty_label = serializers.CharField(source="get_certainty_display", read_only=True)

    class Meta:
        model = Diagnosis
        fields = (
            "id",
            "code",
            "title",
            "label",
            "chapter",
            "certainty",
            "certainty_label",
            "is_primary",
            "is_chronic",
            "note",
            "diagnosed_at",
            "diagnosed_by_name",
        )

    def get_diagnosed_by_name(self, obj: Diagnosis) -> str:
        user = obj.diagnosed_by
        return (user.get_full_name() or user.email) if user else ""


class ProblemSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Problem
        fields = (
            "id",
            "code",
            "title",
            "label",
            "chapter",
            "status",
            "status_label",
            "is_active",
            "onset_on",
            "resolved_on",
            "note",
            "last_noted_at",
        )


class AddDiagnosisInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=12)
    certainty = serializers.ChoiceField(
        choices=Certainty.choices, required=False, default=Certainty.SUSPECTED
    )
    is_primary = serializers.BooleanField(required=False, default=False)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class AddProblemInputSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=12)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    onset_on = serializers.DateField(required=False, allow_null=True)


class ProblemActionInputSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["resolve", "reopen"])
    note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


def _not_found(what: str) -> Response:
    return Response({"detail": what, "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)


def _refused(exc: Exception) -> Response:
    return Response({"detail": str(exc), "code": "cannot_code"}, status=status.HTTP_400_BAD_REQUEST)


class CodeSearchView(CuraviaAPIView):
    """
    Look up a diagnosis code.

    Writes nothing and touches no patient, so the consultation screen calls
    it as the doctor types.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.read"]
    throttle_scope = "search"

    @extend_schema(
        parameters=[OpenApiParameter("q", str, required=False)],
        responses={200: OpenApiResponse(description="Matching codes")},
    )
    def get(self, request: CuraviaRequest) -> Response:
        query = request.query_params.get("q", "")
        found = diagnoses.search(query) if query else []
        return Response(
            {
                "results": [
                    {
                        "code": item.code,
                        "title": item.title,
                        "label": item.label,
                        "chapter": item.chapter,
                        "chronic": item.chronic,
                    }
                    for item in found
                ],
                "chapters": icd10.chapters(),
                "total_in_catalogue": len(icd10.CODES),
            }
        )


class EncounterDiagnosesView(CuraviaAPIView):
    """The codes on one visit."""

    permission_classes = [IsAuthenticated, HasPermission]

    def get_permissions(self):  # type: ignore[no-untyped-def]
        self.required_permissions = (
            ["clinical.note.write"] if self.request.method == "POST" else ["clinical.note.read"]
        )
        return super().get_permissions()

    @extend_schema(responses={200: DiagnosisSerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        encounter = Encounter.objects.filter(pk=pk).first()
        if encounter is None:
            return _not_found("No such visit.")
        rows = diagnoses.for_encounter(encounter)
        return Response({"diagnoses": DiagnosisSerializer(rows, many=True).data})

    @extend_schema(
        request=AddDiagnosisInputSerializer,
        responses={201: DiagnosisSerializer},
        operation_id="clinical_add_diagnosis",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = AddDiagnosisInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return _not_found("No such visit.")

        try:
            diagnosis = diagnoses.add(
                encounter=encounter,
                code=data["code"],
                certainty=data["certainty"],
                is_primary=data["is_primary"],
                note=data["note"],
                actor=request.user,
            )
        except diagnoses.DiagnosisError as exc:
            return _refused(exc)

        audit.record(
            "clinical.diagnosis.record",
            actor=request.user,
            object_type="Diagnosis",
            object_id=diagnosis.pk,
            patient_id=encounter.patient_id,
            changes={
                "code": diagnosis.code,
                "certainty": diagnosis.certainty,
                "primary": diagnosis.is_primary,
            },
            **as_audit_kwargs(),
        )
        return Response(DiagnosisSerializer(diagnosis).data, status=status.HTTP_201_CREATED)


class DiagnosisDetailView(CuraviaAPIView):
    """Promote, rule out, or take off a code."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.write"]

    @extend_schema(
        request=serializers.Serializer,
        responses={200: DiagnosisSerializer},
        operation_id="clinical_change_diagnosis",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        # `request.data` is a list for a JSON array body, which a client
        # posting the wrong shape would send.
        body: Any = request.data if hasattr(request.data, "get") else {}
        action = str(body.get("action", ""))
        note = str(body.get("note", ""))[:300]

        diagnosis = Diagnosis.objects.filter(pk=pk, archived_at__isnull=True).first()
        if diagnosis is None:
            return _not_found("No such diagnosis.")

        try:
            if action == "primary":
                diagnoses.set_primary(diagnosis=diagnosis, actor=request.user)
            elif action == "rule_out":
                diagnoses.rule_out(diagnosis=diagnosis, actor=request.user, note=note)
            else:
                return Response(
                    {"detail": f"There is no action called {action!r}.", "code": "unknown_action"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except diagnoses.DiagnosisError as exc:
            return _refused(exc)

        audit.record(
            f"clinical.diagnosis.{action}",
            actor=request.user,
            object_type="Diagnosis",
            object_id=diagnosis.pk,
            patient_id=diagnosis.patient_id,
            changes={"code": diagnosis.code},
            **as_audit_kwargs(),
        )
        return Response(DiagnosisSerializer(diagnosis).data)

    @extend_schema(responses={204: OpenApiResponse(description="Taken off the visit")})
    def delete(self, request: CuraviaRequest, pk: str) -> Response:
        diagnosis = Diagnosis.objects.filter(pk=pk, archived_at__isnull=True).first()
        if diagnosis is None:
            return _not_found("No such diagnosis.")

        diagnoses.remove(diagnosis=diagnosis, actor=request.user)
        audit.record(
            "clinical.diagnosis.remove",
            actor=request.user,
            object_type="Diagnosis",
            object_id=diagnosis.pk,
            patient_id=diagnosis.patient_id,
            changes={"code": diagnosis.code},
            **as_audit_kwargs(),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProblemListView(CuraviaAPIView):
    """One patient's problem list."""

    permission_classes = [IsAuthenticated, HasPermission]

    def get_permissions(self):  # type: ignore[no-untyped-def]
        self.required_permissions = (
            ["clinical.note.write"] if self.request.method == "POST" else ["clinical.note.read"]
        )
        return super().get_permissions()

    @extend_schema(responses={200: ProblemSerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        from apps.patients.models import Patient

        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return _not_found("No such patient.")

        rows = diagnoses.problem_list(patient)
        audit.record_view(
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            fields=["problem_list"],
            **as_audit_kwargs(),
        )
        return Response({"problems": ProblemSerializer(rows, many=True).data})

    @extend_schema(
        request=AddProblemInputSerializer,
        responses={201: ProblemSerializer},
        operation_id="clinical_add_problem",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        from apps.patients.models import Patient

        serializer = AddProblemInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return _not_found("No such patient.")

        try:
            problem = diagnoses.add_problem(
                organisation_id=patient.organisation_id,
                patient=patient,
                code=data["code"],
                note=data["note"],
                onset_on=data.get("onset_on"),
                actor=request.user,
            )
        except diagnoses.DiagnosisError as exc:
            return _refused(exc)

        audit.record(
            "clinical.problem.add",
            actor=request.user,
            object_type="Problem",
            object_id=problem.pk,
            patient_id=patient.pk,
            changes={"code": problem.code},
            **as_audit_kwargs(),
        )
        return Response(ProblemSerializer(problem).data, status=status.HTTP_201_CREATED)


class ProblemDetailView(CuraviaAPIView):
    """Resolve a problem, or reopen one that has come back."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.write"]

    @extend_schema(
        request=ProblemActionInputSerializer,
        responses={200: ProblemSerializer},
        operation_id="clinical_change_problem",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = ProblemActionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        problem = Problem.objects.filter(pk=pk).first()
        if problem is None:
            return _not_found("No such problem.")

        try:
            if data["action"] == "resolve":
                diagnoses.resolve_problem(problem=problem, actor=request.user, note=data["note"])
            else:
                diagnoses.reopen_problem(problem=problem, actor=request.user)
        except diagnoses.DiagnosisError as exc:
            return _refused(exc)

        audit.record(
            f"clinical.problem.{data['action']}",
            actor=request.user,
            object_type="Problem",
            object_id=problem.pk,
            patient_id=problem.patient_id,
            changes={"code": problem.code, "status": problem.status},
            **as_audit_kwargs(),
        )
        return Response(ProblemSerializer(problem).data)


__all__ = [
    "CodeSearchView",
    "DiagnosisDetailView",
    "EncounterDiagnosesView",
    "ProblemDetailView",
    "ProblemListView",
]
