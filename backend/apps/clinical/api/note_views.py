"""
Consultation notes over HTTP.

Its own module rather than more of ``views.py``: triage and the consultation
note are written by different people on different screens, and the two sets
of views were starting to be read past rather than read.

Four permissions, deliberately separate — ``clinical.note.read``,
``.write``, ``.sign`` and ``.amend``. A house officer may write and sign
their own notes without being able to amend a consultant's. A pharmacist may
read a note without being able to write one. Collapsing them into one code
would be simpler and would lose every one of those distinctions.

Every read of a note is audited, including the ones that find nothing. A
clinical record is the thing the whole audit log exists for.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.clinical import note_templates, notes
from apps.clinical.models import ClinicalNote
from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.journey.models import Encounter

#: What a reader without ``clinical.sensitive.view`` sees in place of the
#: wording. The note's existence is never hidden — a record with invisible
#: entries is a record nobody can trust — only its contents.
REDACTED = {
    "_redacted": (
        "This note is marked sensitive. Your role cannot read it. "
        "Ask the clinician who wrote it, or whoever administers Curavia here."
    )
}


class ClinicalNoteSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    signature = serializers.SerializerMethodField()
    content = serializers.SerializerMethodField()
    is_signed = serializers.BooleanField(read_only=True)
    template_name = serializers.SerializerMethodField()
    may_edit = serializers.SerializerMethodField()

    class Meta:
        model = ClinicalNote
        fields = (
            "id",
            "encounter",
            "patient",
            "template_key",
            "template_name",
            "content",
            "status",
            "status_label",
            "is_signed",
            "thread",
            "version",
            "supersedes",
            "is_current",
            "amendment_reason",
            "author_name",
            "signed_at",
            "signature",
            "content_hash",
            "is_sensitive",
            "last_saved_at",
            "may_edit",
        )

    def get_author_name(self, obj: ClinicalNote) -> str:
        user = obj.author
        return (user.get_full_name() or user.email) if user else ""

    def get_template_name(self, obj: ClinicalNote) -> str:
        template = note_templates.get(obj.template_key)
        return template.name if template else obj.template_key

    def get_signature(self, obj: ClinicalNote) -> list[str]:
        return obj.signature_block()

    def get_content(self, obj: ClinicalNote) -> dict[str, Any]:
        if obj.is_sensitive and not self.context.get("may_read_sensitive", False):
            return dict(REDACTED)
        return obj.content or {}

    def get_may_edit(self, obj: ClinicalNote) -> bool:
        """Whether *this* reader can type into it. A draft, and theirs."""
        reader = self.context.get("reader_id")
        return not obj.is_signed and reader is not None and str(obj.author_id) == str(reader)


class OpenDraftInputSerializer(serializers.Serializer):
    template_key = serializers.CharField(
        max_length=32, required=False, default=note_templates.DEFAULT_TEMPLATE
    )


class AutosaveInputSerializer(serializers.Serializer):
    content = serializers.DictField(required=False)
    is_sensitive = serializers.BooleanField(required=False)


class AmendInputSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)


def _context(request: CuraviaRequest) -> dict[str, Any]:
    return {
        "reader_id": getattr(request.user, "pk", None),
        "may_read_sensitive": "clinical.sensitive.view"
        in getattr(request, "tenant_permissions", frozenset()),
    }


def _not_found(what: str) -> Response:
    return Response({"detail": what, "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)


def _refused(exc: Exception, code: str = "cannot_write") -> Response:
    locked = isinstance(exc, notes.NoteLocked)
    return Response(
        {"detail": str(exc), "code": "note_locked" if locked else code},
        status=status.HTTP_409_CONFLICT if locked else status.HTTP_400_BAD_REQUEST,
    )


class NoteTemplateListView(CuraviaAPIView):
    """
    The note shapes a clinician can choose from.

    Read with ``clinical.note.read`` rather than ``.write``: the screen that
    displays a dental chart needs to know what the columns are even for a
    reader who will never fill one in.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.read"]

    @extend_schema(responses={200: OpenApiResponse(description="The note templates")})
    def get(self, request: CuraviaRequest) -> Response:
        return Response(
            {
                "templates": note_templates.catalogue(),
                "default": note_templates.DEFAULT_TEMPLATE,
                "teeth": list(note_templates.ADULT_TEETH),
                "tooth_findings": list(note_templates.TOOTH_FINDINGS),
            }
        )


class ConsultationNotesView(CuraviaAPIView):
    """The notes on one visit, and starting a new draft."""

    permission_classes = [IsAuthenticated, HasPermission]

    def get_permissions(self):  # type: ignore[no-untyped-def]
        self.required_permissions = (
            ["clinical.note.write"] if self.request.method == "POST" else ["clinical.note.read"]
        )
        return super().get_permissions()

    @extend_schema(responses={200: ClinicalNoteSerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return _not_found("No such visit.")

        rows = notes.for_encounter(encounter, reader=request.user)
        audit.record_view(
            actor=request.user,
            object_type="Encounter",
            object_id=encounter.pk,
            patient_id=encounter.patient_id,
            fields=["clinical_notes"],
            **as_audit_kwargs(),
        )
        return Response(
            {"notes": ClinicalNoteSerializer(rows, many=True, context=_context(request)).data}
        )

    @extend_schema(
        request=OpenDraftInputSerializer,
        responses={201: ClinicalNoteSerializer},
        operation_id="clinical_open_note_draft",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = OpenDraftInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return _not_found("No such visit.")

        try:
            note = notes.open_draft(
                encounter=encounter,
                author=request.user,
                template_key=serializer.validated_data["template_key"],
            )
        except notes.NoteError as exc:
            return _refused(exc)

        # Only the first opening is audited. An idempotent call that returns
        # the same draft is not a new event, and recording it would fill the
        # log with a row every time a screen was reloaded.
        if note.version == 1 and notes.is_empty(note.content):
            audit.record(
                "clinical.note.start",
                actor=request.user,
                object_type="ClinicalNote",
                object_id=note.pk,
                patient_id=encounter.patient_id,
                changes={"template": note.template_key},
                **as_audit_kwargs(),
            )
        return Response(
            ClinicalNoteSerializer(note, context=_context(request)).data,
            status=status.HTTP_201_CREATED,
        )


class NoteDetailView(CuraviaAPIView):
    """One note: read it, autosave it, or discard an unsigned draft."""

    permission_classes = [IsAuthenticated, HasPermission]

    def get_permissions(self):  # type: ignore[no-untyped-def]
        self.required_permissions = (
            ["clinical.note.read"] if self.request.method == "GET" else ["clinical.note.write"]
        )
        return super().get_permissions()

    @extend_schema(responses={200: ClinicalNoteSerializer})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        note = ClinicalNote.objects.filter(pk=pk).select_related("author", "patient").first()
        if note is None:
            return _not_found("No such note.")
        if not note.is_signed and note.author_id != request.user.pk:
            # Somebody else's unfinished thinking. Not an error — it exists —
            # but it is not readable until they put their name to it.
            return Response(
                {"detail": "That note has not been signed yet.", "code": "draft"},
                status=status.HTTP_403_FORBIDDEN,
            )

        audit.record_view(
            actor=request.user,
            object_type="ClinicalNote",
            object_id=note.pk,
            patient_id=note.patient_id,
            fields=["content"],
            flags=[audit.AuditFlag.SENSITIVE] if note.is_sensitive else [],
            **as_audit_kwargs(),
        )
        context = _context(request)
        return Response(
            {
                "note": ClinicalNoteSerializer(note, context=context).data,
                "versions": ClinicalNoteSerializer(
                    notes.history(note), many=True, context=context
                ).data,
            }
        )

    @extend_schema(
        request=AutosaveInputSerializer,
        responses={200: ClinicalNoteSerializer},
        operation_id="clinical_autosave_note",
    )
    def patch(self, request: CuraviaRequest, pk: str) -> Response:
        """
        Autosave. Called as the clinician types, so it has to be cheap and it
        has to be honest: the response carries the time the server actually
        stored the words, which is what the screen shows.
        """
        serializer = AutosaveInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        note = ClinicalNote.objects.filter(pk=pk).first()
        if note is None:
            return _not_found("No such note.")

        try:
            if "is_sensitive" in data:
                note.is_sensitive = bool(data["is_sensitive"])
                if not note.is_signed:
                    note.save(update_fields=["is_sensitive", "updated_at"])
            note = notes.autosave(note=note, content=data.get("content"), actor=request.user)
        except notes.NoteError as exc:
            return _refused(exc)

        return Response(ClinicalNoteSerializer(note, context=_context(request)).data)

    @extend_schema(responses={204: OpenApiResponse(description="Discarded")})
    def delete(self, request: CuraviaRequest, pk: str) -> Response:
        note = ClinicalNote.objects.filter(pk=pk).first()
        if note is None:
            return _not_found("No such note.")
        try:
            notes.discard_draft(note=note, actor=request.user)
        except notes.NoteError as exc:
            return _refused(exc)

        audit.record(
            "clinical.note.discard",
            actor=request.user,
            object_type="ClinicalNote",
            object_id=note.pk,
            patient_id=note.patient_id,
            changes={"template": note.template_key},
            **as_audit_kwargs(),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SignNoteView(CuraviaAPIView):
    """Sign a note, which closes it for good."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.sign"]

    @extend_schema(
        request=None,
        responses={200: ClinicalNoteSerializer},
        operation_id="clinical_sign_note",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        note = ClinicalNote.objects.filter(pk=pk).select_related("patient").first()
        if note is None:
            return _not_found("No such note.")

        try:
            note = notes.sign(
                note=note,
                actor=request.user,
                membership=getattr(request, "membership", None),
            )
        except notes.NoteError as exc:
            return _refused(exc, code="cannot_sign")

        audit.record(
            "clinical.note.sign",
            actor=request.user,
            object_type="ClinicalNote",
            object_id=note.pk,
            patient_id=note.patient_id,
            changes={
                "template": note.template_key,
                "version": note.version,
                "hash": note.content_hash,
            },
            flags=[audit.AuditFlag.SENSITIVE] if note.is_sensitive else [],
            **as_audit_kwargs(),
        )
        return Response(ClinicalNoteSerializer(note, context=_context(request)).data)


class AmendNoteView(CuraviaAPIView):
    """Open the next version of a signed note."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.amend"]

    @extend_schema(
        request=AmendInputSerializer,
        responses={201: ClinicalNoteSerializer},
        operation_id="clinical_amend_note",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = AmendInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        note = ClinicalNote.objects.filter(pk=pk).select_related("patient").first()
        if note is None:
            return _not_found("No such note.")

        try:
            amendment = notes.amend(
                note=note,
                reason=serializer.validated_data["reason"],
                actor=request.user,
            )
        except notes.NoteError as exc:
            return _refused(exc, code="cannot_amend")

        audit.record(
            "clinical.note.amend",
            actor=request.user,
            object_type="ClinicalNote",
            object_id=amendment.pk,
            patient_id=amendment.patient_id,
            changes={
                "amends": str(note.pk),
                "version": amendment.version,
                "reason": amendment.amendment_reason,
            },
            **as_audit_kwargs(),
        )
        return Response(
            ClinicalNoteSerializer(amendment, context=_context(request)).data,
            status=status.HTTP_201_CREATED,
        )


class PatientNotesView(CuraviaAPIView):
    """One patient's signed notes, across every visit."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.read"]

    @extend_schema(responses={200: ClinicalNoteSerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        from apps.patients.models import Patient

        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return _not_found("No such patient.")

        rows = notes.for_patient(patient)
        audit.record_view(
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            fields=["clinical_notes"],
            **as_audit_kwargs(),
        )
        return Response(
            {"notes": ClinicalNoteSerializer(rows, many=True, context=_context(request)).data}
        )


__all__ = [
    "AmendNoteView",
    "ConsultationNotesView",
    "NoteDetailView",
    "NoteTemplateListView",
    "PatientNotesView",
    "SignNoteView",
]
