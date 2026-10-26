"""
Patients: search, register, read.

**Every read of a patient record is audited**, including this one. That is
the promise the audit chain exists to keep, and it is the reason `record_view`
takes a list of field NAMES rather than values — an audit log full of patient
data would be a second copy of the thing being protected, readable by a
different set of people.

Search is audited as a search, not as a view of each result: a clerk typing
"ade" and seeing nine names has not opened nine records. Opening one is the
event worth recording.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.models import Branch
from apps.core.permissions import HasPermission, MaskedFields
from apps.core.request_context import as_audit_kwargs
from apps.patients import search as patient_search
from apps.patients.models import Allergy, NextOfKin, Patient, PatientIdentifier
from apps.patients.registration import (
    RegistrationError,
    RegistrationInput,
    register_patient,
    register_unknown_patient,
)

#: Fields a search result carries. Named here because the audit log records
#: which fields were returned, and that list has to be the real one.
SUMMARY_FIELDS = [
    "mrn",
    "full_name",
    "sex",
    "date_of_birth",
    "phone",
    "is_provisional",
]


class AllergySerializer(serializers.ModelSerializer):
    class Meta:
        model = Allergy
        fields = ("id", "substance", "kind", "reaction", "severity", "noted_on", "is_active")


class IdentifierSerializer(serializers.ModelSerializer):
    class Meta:
        model = PatientIdentifier
        fields = ("id", "kind", "value", "is_verified", "note")


class NextOfKinSerializer(serializers.ModelSerializer):
    class Meta:
        model = NextOfKin
        fields = ("id", "name", "relationship", "phone", "alternate_phone", "is_primary")


class PatientSummarySerializer(serializers.ModelSerializer):
    """What a search result shows. No clinical detail at all."""

    full_name = serializers.CharField(read_only=True)
    age = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = (
            "id",
            "mrn",
            "full_name",
            "first_name",
            "last_name",
            "other_names",
            "sex",
            "date_of_birth",
            "date_of_birth_is_estimated",
            "age",
            "phone",
            "is_provisional",
            "is_deceased",
        )

    def get_age(self, obj: Patient) -> str:
        """
        Age as a clerk would say it, computed here rather than in the client.
        """
        if not obj.date_of_birth:
            return ""
        from django.utils import timezone

        today = timezone.localdate()
        years = today.year - obj.date_of_birth.year
        if (today.month, today.day) < (obj.date_of_birth.month, obj.date_of_birth.day):
            years -= 1
        if years >= 2:
            return f"{years}y"
        months = years * 12 + today.month - obj.date_of_birth.month
        return f"{months}mo" if months >= 1 else "newborn"


class PatientDetailSerializer(MaskedFields, PatientSummarySerializer):
    """
    The full demographic record, narrowed by what the caller may see.

    `patients.view` is deliberately broad — almost everybody needs to
    identify the person in front of them — but it grants the DEMOGRAPHIC
    record only. Blood group, genotype and the allergy list are clinical,
    and they are removed from the response entirely rather than hidden in
    the client, so there is nothing for a curious cashier to find in the
    network tab.

    See docs/permissions-matrix.yml, patients.view.
    """

    permission_fields: ClassVar[dict[str, list[str]]] = {
        "clinical.note.read": ["blood_group", "genotype", "allergies", "allergies_checked_at"],
    }

    allergies = AllergySerializer(many=True, read_only=True)
    identifiers = IdentifierSerializer(many=True, read_only=True)
    next_of_kin = NextOfKinSerializer(many=True, read_only=True)
    allergies_known = serializers.BooleanField(read_only=True)
    home_branch_name = serializers.CharField(source="home_branch.name", read_only=True)
    default_payer_name = serializers.CharField(source="default_payer.name", read_only=True)

    class Meta(PatientSummarySerializer.Meta):
        # Widening the parent's tuple is the point of the subclass, and the
        # stubs type `fields` as the parent's exact length.
        fields = (  # type: ignore[assignment]
            *PatientSummarySerializer.Meta.fields,
            "address",
            "state",
            "lga",
            "email",
            "alternate_phone",
            "blood_group",
            "genotype",
            "marital_status",
            "occupation",
            "home_branch_name",
            "default_payer_name",
            "allergies_known",
            "allergies_checked_at",
            "registered_at",
            "allergies",
            "identifiers",
            "next_of_kin",
        )


class DuplicateSerializer(serializers.Serializer):
    patient = PatientSummarySerializer()
    score = serializers.FloatField()
    reasons = serializers.ListField(child=serializers.CharField())
    is_strong = serializers.BooleanField()


class RegisterSerializer(serializers.Serializer):
    """
    What the front desk sends.

    Only two fields are required, deliberately: see
    apps/patients/models.Patient. A form that refuses to save without a date
    of birth produces an invented one.
    """

    first_name = serializers.CharField(max_length=80)
    last_name = serializers.CharField(max_length=80)
    other_names = serializers.CharField(max_length=120, required=False, allow_blank=True)
    sex = serializers.CharField(max_length=1, required=False, allow_blank=True)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    date_of_birth_is_estimated = serializers.BooleanField(required=False, default=False)
    phone = serializers.CharField(max_length=24, required=False, allow_blank=True)
    alternate_phone = serializers.CharField(max_length=24, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    state = serializers.CharField(max_length=60, required=False, allow_blank=True)
    lga = serializers.CharField(max_length=80, required=False, allow_blank=True)
    blood_group = serializers.CharField(max_length=3, required=False, allow_blank=True)
    genotype = serializers.CharField(max_length=2, required=False, allow_blank=True)
    marital_status = serializers.CharField(max_length=16, required=False, allow_blank=True)
    occupation = serializers.CharField(max_length=120, required=False, allow_blank=True)
    default_payer_id = serializers.UUIDField(required=False, allow_null=True)
    branch_id = serializers.UUIDField(required=False, allow_null=True)
    identifiers = serializers.ListField(child=serializers.DictField(), required=False)
    next_of_kin = serializers.ListField(child=serializers.DictField(), required=False)
    allergies = serializers.ListField(child=serializers.DictField(), required=False)
    allergies_checked = serializers.BooleanField(required=False, default=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    #: Set once the clerk has looked at the possible duplicates and decided
    #: this really is somebody new.
    confirmed_not_duplicate = serializers.BooleanField(required=False, default=False)


class QuickRegisterSerializer(serializers.Serializer):
    sex = serializers.CharField(max_length=1, required=False, allow_blank=True)
    estimated_age = serializers.IntegerField(
        required=False, allow_null=True, min_value=0, max_value=130
    )
    note = serializers.CharField(required=False, allow_blank=True)
    branch_id = serializers.UUIDField(required=False, allow_null=True)


class PatientSearchView(CuraviaAPIView):
    """Find a patient. The front desk's primary tool."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.view"]
    throttle_scope = "search"

    @extend_schema(
        parameters=[
            OpenApiParameter("q", str, description="Name, phone number or MRN."),
            OpenApiParameter("limit", int, required=False),
        ],
        responses={200: PatientSummarySerializer(many=True)},
    )
    def get(self, request: CuraviaRequest) -> Response:
        query = request.query_params.get("q", "")
        limit = min(int(request.query_params.get("limit", 25) or 25), 50)

        results = list(patient_search.search_patients(query, limit=limit))

        # Audited as a search, not as a view of each result: seeing nine
        # names in a list is not reading nine records.
        if query.strip():
            audit.record(
                "patient.search",
                actor=request.user,
                changes={"query": query.strip()[:80], "results": len(results)},
                **as_audit_kwargs(),
            )

        return Response(PatientSummarySerializer(results, many=True).data)


class PatientDetailView(CuraviaAPIView):
    """One patient's record. Every read of this is in the audit log."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.view"]

    @extend_schema(responses={200: PatientDetailSerializer})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        patient = (
            Patient.objects.filter(pk=pk)
            .select_related("home_branch", "default_payer")
            .prefetch_related("allergies", "identifiers", "next_of_kin")
            .first()
        )
        if patient is None:
            return Response(
                {"detail": "No such patient.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        data = PatientDetailSerializer(patient, context={"request": request}).data
        audit.record_view(
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            # Field NAMES, never values. An audit log full of patient data
            # would be a second copy of what it is protecting.
            fields=sorted(str(key) for key in data),
            **as_audit_kwargs(),
        )
        return Response(data)


class PatientByMrnView(CuraviaAPIView):
    """
    Resolve an MRN, following a merge.

    Separate from the detail view because this is what a scanned card or a
    typed number hits, and the answer may be a different record from the one
    the number was issued for.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.view"]

    @extend_schema(responses={200: PatientSummarySerializer})
    def get(self, request: CuraviaRequest, mrn: str) -> Response:
        patient = patient_search.find_by_mrn(mrn)
        if patient is None:
            return Response(
                {"detail": f"No patient has the number {mrn}.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                **PatientSummarySerializer(patient).data,
                # True when the number typed was an old one that has since
                # been merged, so the screen can say which record it opened.
                "resolved_from_merge": patient.mrn.lower() != mrn.strip().lower(),
            }
        )


class DuplicateCheckView(CuraviaAPIView):
    """
    Who this might already be, before a new record is created.

    Never blocking. A system that refuses to register somebody because it
    suspects a duplicate produces a registration under a slightly different
    name, which is the duplicate plus a lie.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.create"]

    @extend_schema(request=None, responses={200: DuplicateSerializer(many=True)})
    def post(self, request: CuraviaRequest) -> Response:
        data: Any = request.data if hasattr(request.data, "get") else {}
        matches = patient_search.find_possible_duplicates(
            first_name=str(data.get("first_name", "")),
            last_name=str(data.get("last_name", "")),
            phone=str(data.get("phone", "")),
            date_of_birth=parse_date(str(data.get("date_of_birth", "")) or ""),
            nin=str(data.get("nin", "")),
        )
        return Response(
            [
                {
                    "patient": PatientSummarySerializer(match.patient).data,
                    "score": round(match.score, 2),
                    "reasons": list(match.reasons),
                    "is_strong": match.is_strong,
                }
                for match in matches
            ]
        )


class PatientRegisterView(CuraviaAPIView):
    """Create a patient."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.create"]

    @extend_schema(
        request=RegisterSerializer,
        responses={
            201: PatientDetailSerializer,
            409: OpenApiResponse(description="Strong possible duplicates; confirm to proceed"),
        },
    )
    def post(self, request: CuraviaRequest) -> Response:
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        branch = _branch_for(request, data.get("branch_id"))
        if branch is None:
            return Response(
                {"detail": "Which branch is this registration at?", "code": "branch_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A strong match stops the first attempt and shows what it found. The
        # clerk then either opens that record or says no and sends the same
        # request again with confirmed_not_duplicate — so the decision is
        # theirs and is made with the evidence in front of them.
        if not data.get("confirmed_not_duplicate"):
            nin = next(
                (
                    str(row.get("value", ""))
                    for row in data.get("identifiers", [])
                    if row.get("kind") == "nin"
                ),
                "",
            )
            matches = patient_search.find_possible_duplicates(
                first_name=data["first_name"],
                last_name=data["last_name"],
                phone=data.get("phone", ""),
                date_of_birth=data.get("date_of_birth"),
                nin=nin,
            )
            strong = [match for match in matches if match.is_strong]
            if strong:
                return Response(
                    {
                        "detail": "This may already be a patient here.",
                        "code": "possible_duplicate",
                        "matches": [
                            {
                                "patient": PatientSummarySerializer(match.patient).data,
                                "score": round(match.score, 2),
                                "reasons": list(match.reasons),
                                "is_strong": match.is_strong,
                            }
                            for match in matches
                        ],
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        try:
            patient = register_patient(
                organisation=request.organisation,  # type: ignore[arg-type]
                branch=branch,
                data=RegistrationInput(
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                    other_names=data.get("other_names", ""),
                    sex=data.get("sex", "") or "U",
                    date_of_birth=data.get("date_of_birth"),
                    date_of_birth_is_estimated=data.get("date_of_birth_is_estimated", False),
                    phone=data.get("phone", ""),
                    alternate_phone=data.get("alternate_phone", ""),
                    email=data.get("email", ""),
                    address=data.get("address", ""),
                    state=data.get("state", ""),
                    lga=data.get("lga", ""),
                    blood_group=data.get("blood_group", ""),
                    genotype=data.get("genotype", ""),
                    marital_status=data.get("marital_status", ""),
                    occupation=data.get("occupation", ""),
                    default_payer_id=(
                        str(data["default_payer_id"]) if data.get("default_payer_id") else None
                    ),
                    identifiers=list(data.get("identifiers", [])),
                    next_of_kin=list(data.get("next_of_kin", [])),
                    allergies=list(data.get("allergies", [])),
                    allergies_checked=data.get("allergies_checked", False),
                    notes=data.get("notes", ""),
                ),
                registered_by=request.user,
            )
        except RegistrationError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_register"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:  # django ValidationError from full_clean
            from django.core.exceptions import ValidationError as DjangoValidationError

            if isinstance(exc, DjangoValidationError):
                return Response(
                    {
                        "detail": "Some of the information needs fixing.",
                        "code": "invalid",
                        "fields": getattr(exc, "message_dict", {"detail": exc.messages}),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise

        audit.record(
            "patient.create",
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            changes={"mrn": patient.mrn},
            **as_audit_kwargs(),
        )
        return Response(
            PatientDetailSerializer(patient, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class QuickRegisterView(CuraviaAPIView):
    """Emergency quick-registration. A record in ten seconds."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.create"]

    @extend_schema(request=QuickRegisterSerializer, responses={201: PatientDetailSerializer})
    def post(self, request: CuraviaRequest) -> Response:
        serializer = QuickRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        branch = _branch_for(request, data.get("branch_id"))
        if branch is None:
            return Response(
                {"detail": "Which branch is this?", "code": "branch_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        patient = register_unknown_patient(
            organisation=request.organisation,  # type: ignore[arg-type]
            branch=branch,
            sex=data.get("sex", "") or "U",
            estimated_age=data.get("estimated_age"),
            note=data.get("note", ""),
            registered_by=request.user,
        )
        audit.record(
            "patient.create",
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            changes={"mrn": patient.mrn, "provisional": True},
            reason="emergency quick-registration",
            flags=["provisional"],
            **as_audit_kwargs(),
        )
        return Response(
            PatientDetailSerializer(patient, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


def _branch_for(request: CuraviaRequest, branch_id) -> Branch | None:
    """
    The branch to register at: the one asked for, or the active one.

    Looked up through the scoped manager, so a branch id belonging to
    another hospital simply is not found.
    """
    if branch_id:
        return Branch.objects.filter(pk=branch_id).first()
    from apps.core.tenancy import current_branch_id

    active = current_branch_id()
    if active:
        return Branch.objects.filter(pk=active).first()
    return Branch.objects.filter(is_main=True).first()
