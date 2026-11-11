"""
Consent and issued documents over HTTP.

Reading either is audited, including the reads that find nothing. A consent
form and a medical report are both parts of the clinical record, which is
the thing the audit log exists for.

The verification endpoint is the exception to everything else in this file:
no authentication, no hospital in scope, and a payload built by one function
that is the only thing allowed to decide what a stranger may see.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.documents import catalogue
from apps.documents import service as documents
from apps.documents.models import Consent, ConsentMethod, ConsentStatus, Document
from apps.journey.models import Encounter
from apps.patients.models import Patient


class ConsentSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    form_name = serializers.CharField(read_only=True)
    statements = serializers.ListField(child=serializers.CharField(), read_only=True)
    declaration = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = Consent
        fields = (
            "id",
            "form_key",
            "form_name",
            "form_version",
            "statements",
            "declaration",
            "status",
            "status_label",
            "method",
            "method_label",
            "subject",
            "given_by_name",
            "given_by_relationship",
            "witness_name",
            "witness_role",
            "taken_by_name",
            "taken_by_role",
            "given_at",
            "withdrawn_at",
            "withdrawn_reason",
            "refused_reason",
            "extras",
            "is_active",
            "created_at",
        )
        read_only_fields = fields


class DocumentSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    name = serializers.CharField(read_only=True)
    is_issued = serializers.BooleanField(read_only=True)
    # Not "is_valid": DRF's BaseSerializer already has an is_valid() method,
    # and a declared field of that name silently replaces it. mypy caught it;
    # at runtime it would have been a serializer that cannot validate.
    still_valid = serializers.BooleanField(source="is_valid", read_only=True)
    signature = serializers.SerializerMethodField()
    verify_url = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = (
            "id",
            "template_key",
            "template_version",
            "name",
            "content",
            "wording",
            "status",
            "status_label",
            "reference",
            "issued_at",
            "signature",
            "letterhead",
            "revoked_at",
            "revoked_reason",
            "is_issued",
            "still_valid",
            "verify_url",
            "verified_count",
            "created_at",
        )
        read_only_fields = fields

    def get_signature(self, obj: Document) -> list[str]:
        return obj.signature_block()

    def get_verify_url(self, obj: Document) -> str:
        return _verify_url(obj)


# ---------------------------------------------------------------------------
# What a client may send
# ---------------------------------------------------------------------------
#
# Declared rather than read loosely off `request.data`, so the OpenAPI schema
# the frontend's types are generated from describes these routes instead of
# falling back to "some object". The service layer still re-checks
# everything; these say what the shape is, not what the rules are.


class ConsentInputSerializer(serializers.Serializer):
    form_key = serializers.CharField(max_length=32)
    given_by_name = serializers.CharField(max_length=200)
    encounter_id = serializers.UUIDField(required=False, allow_null=True)
    given_by_relationship = serializers.CharField(
        max_length=60, required=False, allow_blank=True, default=""
    )
    method = serializers.ChoiceField(
        choices=ConsentMethod.choices, required=False, default=ConsentMethod.TYPED
    )
    status = serializers.ChoiceField(
        choices=ConsentStatus.choices, required=False, default=ConsentStatus.GIVEN
    )
    subject = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    witness_name = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default=""
    )
    witness_role = serializers.CharField(
        max_length=120, required=False, allow_blank=True, default=""
    )
    refused_reason = serializers.CharField(required=False, allow_blank=True, default="")
    extras = serializers.DictField(required=False)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class DocumentDraftSerializer(serializers.Serializer):
    template_key = serializers.CharField(max_length=32)
    encounter_id = serializers.UUIDField(required=False, allow_null=True)
    content = serializers.DictField(child=serializers.CharField(allow_blank=True), required=False)


class DocumentContentSerializer(serializers.Serializer):
    content = serializers.DictField(child=serializers.CharField(allow_blank=True), required=False)


class PaperworkSerializer(serializers.Serializer):
    """Consents and documents for one visit, so the schema says so."""

    consents = ConsentSerializer(many=True, read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)


def _verify_url(document: Document) -> str:
    """
    Where the QR code points.

    Absolute and built from a setting, never from a Host header: the code is
    printed onto paper that is read months later, outside any request, and a
    caller-controlled host would put an attacker's address on a hospital's
    certificate.
    """
    from django.conf import settings
    from django.urls import reverse

    if not document.token:
        return ""
    base = str(settings.CURAVIA.get("PUBLIC_URL", "")).rstrip("/")
    return f"{base}{reverse('documents:verify', args=[document.token])}"


def _not_found(what: str) -> Response:
    return Response({"detail": what, "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)


def _refused(exc: Exception, code: str = "refused") -> Response:
    return Response({"detail": str(exc), "code": code}, status=status.HTTP_400_BAD_REQUEST)


def _body(request: CuraviaRequest) -> Any:
    return request.data if hasattr(request.data, "get") else {}


def _holds(request: CuraviaRequest, code: str) -> bool:
    """
    A second code on the same route.

    ``required_permissions`` gates the whole view, which is right when GET
    and POST need the same thing. Here they do not: reading a patient's
    consents is wider than taking one, and drafting a document is wider than
    reading it. Rather than split one resource over two URLs, the view is
    gated on the reading code and the writing code is checked here.
    """
    held: frozenset[str] = getattr(request, "tenant_permissions", frozenset())
    return code in held


def _forbidden(detail: str) -> Response:
    return Response({"detail": detail, "code": "forbidden"}, status=status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


class ConsentFormListView(CuraviaAPIView):
    """The forms a patient can be asked to agree to."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["consent.read"]

    @extend_schema(
        responses={200: OpenApiResponse(description="Consent forms")},
        operation_id="consent_forms",
    )
    def get(self, request: CuraviaRequest) -> Response:
        return Response({"forms": catalogue.consent_catalogue()})


class DocumentTemplateListView(CuraviaAPIView):
    """The documents this hospital can issue."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["document.read"]

    @extend_schema(
        responses={200: OpenApiResponse(description="Document templates")},
        operation_id="document_templates",
    )
    def get(self, request: CuraviaRequest) -> Response:
        return Response({"templates": catalogue.document_catalogue()})


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


class PatientConsentsView(CuraviaAPIView):
    """Everything this patient has ever been asked, and what they said."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["consent.read"]

    @extend_schema(responses={200: ConsentSerializer(many=True)}, operation_id="consents_list")
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return _not_found("No such patient.")

        rows = documents.consents_for_patient(patient)
        audit.record_view(
            actor=request.user,
            object_type="Consent",
            object_id=patient.pk,
            patient_id=patient.pk,
            **as_audit_kwargs(),
        )
        return Response({"consents": ConsentSerializer(rows, many=True).data})

    @extend_schema(
        request=ConsentInputSerializer,
        responses={201: ConsentSerializer},
        operation_id="consents_take",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        if not _holds(request, "consent.take"):
            return _forbidden("Your role cannot take consent.")
        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return _not_found("No such patient.")

        body = _body(request)
        encounter = None
        if body.get("encounter_id"):
            encounter = Encounter.objects.filter(pk=body["encounter_id"]).first()
            if encounter is None:
                return _not_found("No such visit.")

        try:
            consent = documents.take_consent(
                patient=patient,
                encounter=encounter,
                form_key=str(body.get("form_key", "")),
                given_by_name=str(body.get("given_by_name", "")),
                given_by_relationship=str(body.get("given_by_relationship", "")),
                method=str(body.get("method", "typed")),
                status=str(body.get("status", "given")),
                subject=str(body.get("subject", "")),
                witness_name=str(body.get("witness_name", "")),
                witness_role=str(body.get("witness_role", "")),
                refused_reason=str(body.get("refused_reason", "")),
                extras=body.get("extras") if isinstance(body.get("extras"), dict) else None,
                actor=request.user,
                membership=getattr(request, "membership", None),
            )
        except documents.DocumentError as exc:
            return _refused(exc, code="cannot_take_consent")

        audit.record(
            "consent.take",
            actor=request.user,
            object_type="Consent",
            object_id=consent.pk,
            patient_id=patient.pk,
            changes={"form": consent.form_key, "outcome": consent.status},
            **as_audit_kwargs(),
        )
        return Response(ConsentSerializer(consent).data, status=status.HTTP_201_CREATED)


class ConsentDetailView(CuraviaAPIView):
    """Withdraw one."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["consent.withdraw"]

    @extend_schema(
        request=ReasonSerializer,
        responses={200: ConsentSerializer},
        operation_id="consents_withdraw",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        consent = Consent.objects.filter(pk=pk).first()
        if consent is None:
            return _not_found("No such consent.")

        try:
            consent = documents.withdraw_consent(
                consent=consent,
                reason=str(_body(request).get("reason", "")),
                actor=request.user,
            )
        except documents.DocumentError as exc:
            return _refused(exc, code="cannot_withdraw")

        audit.record(
            "consent.withdraw",
            actor=request.user,
            object_type="Consent",
            object_id=consent.pk,
            patient_id=consent.patient_id,
            changes={"form": consent.form_key, "reason": consent.withdrawn_reason},
            **as_audit_kwargs(),
        )
        return Response(ConsentSerializer(consent).data)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class PatientDocumentsView(CuraviaAPIView):
    """Everything issued to this patient, and anything still being written."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["document.read"]

    @extend_schema(responses={200: DocumentSerializer(many=True)}, operation_id="documents_list")
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return _not_found("No such patient.")

        rows = documents.documents_for_patient(patient)
        audit.record_view(
            actor=request.user,
            object_type="Document",
            object_id=patient.pk,
            patient_id=patient.pk,
            **as_audit_kwargs(),
        )
        return Response({"documents": DocumentSerializer(rows, many=True).data})

    @extend_schema(
        request=DocumentDraftSerializer,
        responses={201: DocumentSerializer},
        operation_id="documents_draft",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        if not _holds(request, "document.write"):
            return _forbidden("Your role cannot write documents.")
        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return _not_found("No such patient.")

        body = _body(request)
        encounter = None
        if body.get("encounter_id"):
            encounter = Encounter.objects.filter(pk=body["encounter_id"]).first()
            if encounter is None:
                return _not_found("No such visit.")

        content = body.get("content")
        try:
            document = documents.draft_document(
                patient=patient,
                encounter=encounter,
                template_key=str(body.get("template_key", "")),
                content=content if isinstance(content, dict) else {},
                actor=request.user,
            )
        except documents.DocumentError as exc:
            return _refused(exc, code="cannot_draft")

        return Response(DocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class DocumentDetailView(CuraviaAPIView):
    """Read one, or change a draft's wording."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["document.read"]

    @extend_schema(responses={200: DocumentSerializer}, operation_id="documents_get")
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        document = Document.objects.filter(pk=pk).first()
        if document is None:
            return _not_found("No such document.")

        audit.record_view(
            actor=request.user,
            object_type="Document",
            object_id=document.pk,
            patient_id=document.patient_id,
            **as_audit_kwargs(),
        )
        return Response(DocumentSerializer(document).data)

    @extend_schema(
        request=DocumentContentSerializer,
        responses={200: DocumentSerializer},
        operation_id="documents_update_draft",
    )
    def patch(self, request: CuraviaRequest, pk: str) -> Response:
        if not _holds(request, "document.write"):
            return _forbidden("Your role cannot write documents.")
        document = Document.objects.filter(pk=pk).first()
        if document is None:
            return _not_found("No such document.")

        content = _body(request).get("content")
        try:
            document = documents.update_draft(
                document=document,
                content=content if isinstance(content, dict) else {},
                actor=request.user,
            )
        except documents.DocumentError as exc:
            return _refused(exc, code="cannot_update")

        return Response(DocumentSerializer(document).data)


class IssueDocumentView(CuraviaAPIView):
    """
    Sign it and let it out.

    Its own route rather than a flag on the update call. Issuing puts a
    clinician's registration number on paper that leaves the building, and
    it is the act the audit log is asked about.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["document.issue"]

    @extend_schema(
        request=None, responses={200: DocumentSerializer}, operation_id="documents_issue"
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        document = Document.objects.filter(pk=pk).first()
        if document is None:
            return _not_found("No such document.")

        try:
            document = documents.issue(
                document=document,
                actor=request.user,
                membership=getattr(request, "membership", None),
            )
        except documents.DocumentError as exc:
            return _refused(exc, code="cannot_issue")

        audit.record(
            "document.issue",
            actor=request.user,
            object_type="Document",
            object_id=document.pk,
            patient_id=document.patient_id,
            changes={"kind": document.template_key, "reference": document.reference},
            **as_audit_kwargs(),
        )
        return Response(DocumentSerializer(document).data)


class RevokeDocumentView(CuraviaAPIView):
    """Pull one back. The row and its token stay."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["document.revoke"]

    @extend_schema(
        request=ReasonSerializer,
        responses={200: DocumentSerializer},
        operation_id="documents_revoke",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        document = Document.objects.filter(pk=pk).first()
        if document is None:
            return _not_found("No such document.")

        try:
            document = documents.revoke(
                document=document,
                reason=str(_body(request).get("reason", "")),
                actor=request.user,
            )
        except documents.DocumentError as exc:
            return _refused(exc, code="cannot_revoke")

        audit.record(
            "document.revoke",
            actor=request.user,
            object_type="Document",
            object_id=document.pk,
            patient_id=document.patient_id,
            changes={"reference": document.reference, "reason": document.revoked_reason},
            **as_audit_kwargs(),
        )
        return Response(DocumentSerializer(document).data)


class EncounterPaperworkView(CuraviaAPIView):
    """Consents and documents for one visit, in one call."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["document.read"]

    @extend_schema(
        responses={200: PaperworkSerializer},
        operation_id="encounter_paperwork",
    )
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return _not_found("No such visit.")

        consents = documents.consents_for_encounter(encounter)
        rows = documents.documents_for_encounter(encounter)
        audit.record_view(
            actor=request.user,
            object_type="Document",
            object_id=encounter.pk,
            patient_id=encounter.patient_id,
            **as_audit_kwargs(),
        )
        return Response(
            {
                "consents": ConsentSerializer(consents, many=True).data,
                "documents": DocumentSerializer(rows, many=True).data,
            }
        )


# ---------------------------------------------------------------------------
# The public check
# ---------------------------------------------------------------------------


class VerifyDocumentView(CuraviaAPIView):
    """
    Is this piece of paper real?

    Unauthenticated, because the person asking is an employer, a school
    registrar or an immigration officer with a phone and no account. The
    token in the URL is the whole of the authorisation, and what it buys is
    four facts and no diagnosis — built by ``service.verify``, which is the
    only function allowed to decide what that is.
    """

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_scope = "search"

    @extend_schema(
        parameters=[OpenApiParameter("token", str, location="path")],
        responses={200: OpenApiResponse(description="What the document is")},
        operation_id="documents_verify",
    )
    def get(self, request: CuraviaRequest, token: str) -> Response:
        found = documents.verify(token)
        if found is None:
            return Response(
                {
                    "detail": (
                        "No document with that code was issued by any hospital using Curavia."
                    ),
                    "code": "not_found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        documents.record_verification(token)
        return Response(found)


__all__ = [
    "ConsentDetailView",
    "ConsentFormListView",
    "DocumentDetailView",
    "DocumentTemplateListView",
    "EncounterPaperworkView",
    "IssueDocumentView",
    "PatientConsentsView",
    "PatientDocumentsView",
    "RevokeDocumentView",
    "VerifyDocumentView",
]
