"""
Merge, timeline and card endpoints.

Merging is the one action in this module that a clerk cannot simply redo if
they get it wrong, so the API is shaped to make the mistake hard rather than
to make the happy path short:

* The caller must name the winner and the loser explicitly. There is no
  "merge these two" that picks for them.
* There is a preview endpoint, and the screen calls it first. What the clerk
  approves is the outcome, not the intention.
* A reason is required. Not for the audit log's sake — the audit log would
  record it anyway — but because being made to write "same person, card
  reissued after name change" is the step at which somebody notices these
  are two different people.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.patients import card as patient_card
from apps.patients import merge as patient_merge
from apps.patients import timeline as patient_timeline
from apps.patients.api.views import PatientSummarySerializer
from apps.patients.models import Patient, PatientMerge

#: Short enough that nobody writes an essay, long enough for a sentence that
#: explains itself to whoever reads the audit log in two years.
MIN_REASON_LENGTH = 8


class MergeRequestSerializer(serializers.Serializer):
    winner_id = serializers.UUIDField(help_text="The record that survives.")
    loser_id = serializers.UUIDField(help_text="The record folded into it.")
    reason = serializers.CharField(
        min_length=MIN_REASON_LENGTH,
        max_length=500,
        help_text="Why these are the same person.",
    )


class PreviewRequestSerializer(serializers.Serializer):
    winner_id = serializers.UUIDField()
    loser_id = serializers.UUIDField()


class ReverseRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=MIN_REASON_LENGTH, max_length=500)


class TimelineEntrySerializer(serializers.Serializer):
    at = serializers.DateTimeField()
    kind = serializers.CharField()
    title = serializers.CharField()
    detail = serializers.CharField()
    actor = serializers.CharField()
    tone = serializers.CharField()
    is_pinned = serializers.BooleanField()


def _fetch(pk) -> Patient | None:
    """
    One patient, through the scoped manager.

    A record belonging to another hospital is simply not found, which is why
    nothing here checks the organisation itself.
    """
    return Patient.objects.filter(pk=pk).select_related("home_branch", "organisation").first()


def _plan_response(plan: patient_merge.MergePlan) -> dict:
    """
    The preview, in the words the screen shows.

    Field names are translated here rather than in the client, so the audit
    log, the API and the screen all describe a merge the same way.
    """
    labels = {
        field.name: str(field.verbose_name)
        for field in Patient._meta.get_fields()
        if hasattr(field, "verbose_name")
    }
    return {
        "winner": PatientSummarySerializer(plan.winner).data,
        "loser": PatientSummarySerializer(plan.loser).data,
        "fills": [
            {
                "field": name,
                "label": labels.get(name.removesuffix("_id"), name.replace("_", " ")),
                "value": str(becomes),
            }
            for name, (_was, becomes) in sorted(plan.fills.items())
        ],
        "moves": plan.moves,
        "severity_changes": plan.severity_changes,
        "allergies_already_present": plan.allergies_already_present,
        "warnings": plan.warnings,
        "is_safe": plan.is_safe,
    }


class MergePreviewView(CuraviaAPIView):
    """What a merge would do. Always called before the merge itself."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.merge"]

    @extend_schema(
        request=PreviewRequestSerializer,
        responses={200: OpenApiResponse(description="What the merge would change")},
    )
    def post(self, request: CuraviaRequest) -> Response:
        serializer = PreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        winner = _fetch(serializer.validated_data["winner_id"])
        loser = _fetch(serializer.validated_data["loser_id"])
        if winner is None or loser is None:
            return Response(
                {"detail": "One of those records was not found.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            plan = patient_merge.plan_merge(winner=winner, loser=loser)
        except patient_merge.MergeError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_merge"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        suggested_winner, _suggested_loser = patient_merge.suggest_winner(winner, loser)
        return Response(
            {
                **_plan_response(plan),
                # An opinion, shown as one: "we would keep this one". The
                # clerk still chooses.
                "suggested_winner_id": str(suggested_winner.pk),
            }
        )


class MergeView(CuraviaAPIView):
    """Do the merge."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.merge"]

    @extend_schema(
        request=MergeRequestSerializer,
        responses={
            201: OpenApiResponse(description="Merged"),
            400: OpenApiResponse(description="The merge was refused"),
        },
    )
    def post(self, request: CuraviaRequest) -> Response:
        serializer = MergeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        winner = _fetch(data["winner_id"])
        loser = _fetch(data["loser_id"])
        if winner is None or loser is None:
            return Response(
                {"detail": "One of those records was not found.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # The MRNs are read before the merge, because afterwards the loser's
        # record is reachable only through the pointer and the audit entry
        # should say what a human saw on screen.
        loser_mrn, winner_mrn = loser.mrn, winner.mrn

        try:
            record = patient_merge.merge_patients(
                winner=winner, loser=loser, actor=request.user, reason=data["reason"]
            )
        except patient_merge.MergeError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_merge"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit.record(
            "patient.merge",
            actor=request.user,
            object_type="Patient",
            object_id=winner.pk,
            patient_id=winner.pk,
            changes={
                "kept": winner_mrn,
                "merged": loser_mrn,
                # The manifest, so the audit log alone is enough to see what
                # a merge did without reading the merge table.
                "moved": record.manifest.get("moved", {}),
                "filled": sorted(record.manifest.get("filled", {})),
            },
            reason=data["reason"],
            flags=[audit.AuditFlag.SENSITIVE],
            **as_audit_kwargs(),
        )
        return Response(
            {
                "id": str(record.pk),
                "winner": PatientSummarySerializer(winner).data,
                "loser": PatientSummarySerializer(loser).data,
                "manifest": record.manifest,
            },
            status=status.HTTP_201_CREATED,
        )


class MergeReverseView(CuraviaAPIView):
    """Undo one merge."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.merge"]

    @extend_schema(
        request=ReverseRequestSerializer,
        responses={200: OpenApiResponse(description="Reversed")},
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = ReverseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        record = PatientMerge.objects.filter(pk=pk).select_related("winner", "loser").first()
        if record is None:
            return Response(
                {"detail": "No such merge.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            patient_merge.reverse_merge(
                record, actor=request.user, reason=serializer.validated_data["reason"]
            )
        except patient_merge.MergeError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_reverse"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit.record(
            "patient.merge.reverse",
            actor=request.user,
            object_type="Patient",
            object_id=record.winner_id,
            patient_id=record.winner_id,
            changes={"kept": record.winner.mrn, "restored": record.loser.mrn},
            reason=serializer.validated_data["reason"],
            flags=[audit.AuditFlag.SENSITIVE],
            **as_audit_kwargs(),
        )
        return Response({"id": str(record.pk), "reversed_at": record.reversed_at})


class PatientMergeHistoryView(CuraviaAPIView):
    """
    Every merge this record has been part of, won or lost.

    Separate from the timeline because this is the screen a records officer
    uses to find a merge in order to reverse it, and it carries the merge
    ids the timeline deliberately does not.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.merge"]

    @extend_schema(responses={200: OpenApiResponse(description="Merges involving this record")})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        from django.db.models import Q

        records = (
            PatientMerge.objects.filter(Q(winner_id=pk) | Q(loser_id=pk))
            .select_related("winner", "loser", "by", "reversed_by")
            .order_by("-at")[:50]
        )
        return Response(
            [
                {
                    "id": str(record.pk),
                    "at": record.at,
                    "by": patient_timeline.name_of(record.by),
                    "reason": record.reason,
                    "winner": {"id": str(record.winner_id), "mrn": record.winner.mrn},
                    "loser": {"id": str(record.loser_id), "mrn": record.loser.mrn},
                    "is_reversed": record.is_reversed,
                    "reversed_at": record.reversed_at,
                    "reversed_by": patient_timeline.name_of(record.reversed_by),
                    "reversal_reason": record.reversal_reason,
                    "can_reverse": not record.is_reversed and str(record.winner_id) == str(pk),
                }
                for record in records
            ]
        )


class PatientTimelineView(CuraviaAPIView):
    """
    Everything that has happened to this patient here.

    Audited as a view of the record, because that is what it is: scrolling
    somebody's history is reading their record, whatever screen it is drawn
    on.

    **Needs both codes**, and `HasPermission` requires every one listed. So
    the front desk cannot open this, even though it can open the demographic
    record and even though today the timeline holds little more than a
    registration. That is deliberate and is about where the line ends up,
    not where it starts: by milestone 7 this list carries diagnoses and
    results, and a permission that was widened once because the screen
    looked harmless does not get narrowed again afterwards. What the front
    desk needs — when somebody last attended, what is booked — is the visit
    list, which is a different endpoint on a different permission.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.view", "clinical.note.read"]

    @extend_schema(responses={200: TimelineEntrySerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        patient = _fetch(pk)
        if patient is None:
            return Response(
                {"detail": "No such patient.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        entries = patient_timeline.build_timeline(patient)
        audit.record_view(
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            fields=["timeline"],
            **as_audit_kwargs(),
        )
        return Response(
            {
                "entries": TimelineEntrySerializer(entries, many=True).data,
                # What this timeline does not carry yet, and when it will.
                # An empty timeline must not read as "nothing happened".
                "pending": list(patient_timeline.PENDING_SOURCES),
            }
        )


class PatientCardView(CuraviaAPIView):
    """
    The printable card.

    Audited: a card carries the patient's name and number out of the
    building, so issuing one is a disclosure and the log should say who
    printed it.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.view"]

    @extend_schema(responses={200: OpenApiResponse(description="Card contents and QR code")})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        patient = _fetch(pk)
        if patient is None:
            return Response(
                {"detail": "No such patient.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            card = patient_card.build_card(
                patient, support_phone=getattr(request.organisation, "contact_phone", "") or ""
            )
        except patient_card.CardError as exc:
            return Response(
                {"detail": str(exc), "code": "no_card"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit.record(
            "patient.card.print",
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            changes={"mrn": patient.mrn},
            **as_audit_kwargs(),
        )
        return Response(
            {
                "patient": PatientSummarySerializer(patient).data,
                "printed_name": card.printed_name,
                "hospital_name": card.hospital_name,
                "branch_name": card.branch_name,
                "payload": card.payload,
                "qr_svg": card.qr_svg,
                "support_phone": card.support_phone,
            }
        )


class ScanView(CuraviaAPIView):
    """
    Resolve a scanned card.

    Takes whatever the scanner read, which may be a Curavia payload, a bare
    MRN typed by hand, or something else entirely — a clerk pointing a
    scanner at the wrong thing is a normal Tuesday. Anything unrecognised
    gets a plain answer rather than a 500.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["patients.view"]

    @extend_schema(request=None, responses={200: PatientSummarySerializer})
    def post(self, request: CuraviaRequest) -> Response:
        from apps.patients import search as patient_search

        # `request.data` is a list for a JSON array body, which a scanner
        # driver posting the wrong shape would send.
        body: Any = request.data if hasattr(request.data, "get") else {}
        raw = str(body.get("code", "")).strip()
        parsed = patient_card.parse_payload(raw)

        if parsed is not None:
            slug, mrn = parsed
            if request.organisation is not None and slug != request.organisation.slug:
                # A card from another hospital. Say so plainly: the clerk is
                # holding something real, just not theirs.
                return Response(
                    {
                        "detail": "That card was issued by a different hospital.",
                        "code": "other_hospital",
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            mrn = raw

        patient = patient_search.find_by_mrn(mrn) if mrn else None
        if patient is None:
            return Response(
                {"detail": "Nothing here matches that code.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {
                **PatientSummarySerializer(patient).data,
                "resolved_from_merge": patient.mrn.lower() != mrn.strip().lower(),
            }
        )
