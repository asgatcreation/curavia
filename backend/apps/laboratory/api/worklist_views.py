"""
The laboratory worklist over HTTP.

Three audiences read this, and the permissions follow them rather than the
data. A **doctor** sees where their tests have got to (``lab.order.view``). A
**nurse** sees what has to be drawn, and draws it (``lab.sample.collect``). A
**laboratory** books tubes in and refuses the unusable ones
(``lab.sample.receive``). The one that matters is the third: a ward that could
mark its own samples as received would be closing its own loop, and the gap
between "we sent it" and "we have it" is exactly where samples are lost.

``required_permissions`` gates a whole route, so a route that both reads and
acts checks the stronger code inside the method against
``request.tenant_permissions`` — the pattern used throughout this codebase.

**Labels come back as a PDF, not as HTML.** See ``apps.laboratory.labels``: a
barcode a browser decided to scale is a barcode that will not scan.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer
from rest_framework.response import Response

from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.core.tenancy import current_branch_id
from apps.laboratory import labels as label_render
from apps.laboratory import worklist
from apps.laboratory.models import (
    LabRequest,
    RejectionReason,
    RequestStatus,
    Sample,
    SampleStatus,
)


class PdfRenderer(BaseRenderer):
    """
    Declares that a route can answer with a PDF.

    Nothing is rendered through it - the label views return an ordinary
    ``HttpResponse`` carrying bytes. It exists because DRF negotiates the
    content type **before** the handler runs, and a view that advertises
    only JSON answers a browser asking for ``application/pdf`` with 406
    before any of our code is reached.
    """

    media_type = "application/pdf"
    format = "pdf"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


READ = ["lab.order.view"]
COLLECT_CODE = "lab.sample.collect"
RECEIVE_CODE = "lab.sample.receive"

DENIED = {"detail": "Your role does not allow this.", "code": "permission_denied"}


def _holds(request: CuraviaRequest, code: str) -> bool:
    return code in getattr(request, "tenant_permissions", frozenset())


# --------------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------------


class SampleSerializer(serializers.ModelSerializer):
    specimen_label = serializers.CharField(source="get_specimen_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    mrn = serializers.CharField(source="patient.mrn", read_only=True)
    #: "Clotted - drawn through a 25G needle", formatted once on the server.
    rejection = serializers.CharField(source="rejection_label", read_only=True)
    minutes_in_transit = serializers.IntegerField(read_only=True)
    replaces = serializers.CharField(source="recollection_of.accession", read_only=True, default="")

    class Meta:
        model = Sample
        fields = (
            "id",
            "accession",
            "patient",
            "patient_name",
            "mrn",
            "specimen",
            "specimen_label",
            "container",
            "panel_names",
            "status",
            "status_label",
            "collected_at",
            "collection_note",
            "received_at",
            "rejected_at",
            "rejection_reason",
            "rejection_note",
            "rejection",
            "minutes_in_transit",
            "replaces",
            "label_print_count",
        )


class LabRequestSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="panel.code", read_only=True)
    name = serializers.CharField(source="panel.name", read_only=True)
    specimen_label = serializers.CharField(source="get_specimen_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    mrn = serializers.CharField(source="patient.mrn", read_only=True)
    accession = serializers.CharField(source="sample.accession", read_only=True, default="")
    #: Computed against the server's clock, not the browser's. A tablet with
    #: the wrong time must not decide what is overdue.
    overdue = serializers.BooleanField(source="is_overdue", read_only=True)
    minutes_late = serializers.IntegerField(read_only=True)
    is_send_out = serializers.BooleanField(source="panel.is_send_out", read_only=True)

    class Meta:
        model = LabRequest
        fields = (
            "id",
            "order",
            "patient",
            "patient_name",
            "mrn",
            "panel",
            "code",
            "name",
            "specimen",
            "specimen_label",
            "container",
            "priority",
            "status",
            "status_label",
            "sample",
            "accession",
            "requested_at",
            "due_at",
            "overdue",
            "minutes_late",
            "rejection_count",
            "is_send_out",
        )


class TubeSerializer(serializers.Serializer):
    """One tube to draw. See ``worklist.plan_collection``."""

    specimen = serializers.CharField()
    specimen_label = serializers.CharField()
    container = serializers.CharField(allow_blank=True)
    panels = serializers.ListField(child=serializers.DictField())


class CountsSerializer(serializers.Serializer):
    awaiting_collection = serializers.IntegerField()
    redraws = serializers.IntegerField()
    in_transit = serializers.IntegerField()
    in_laboratory = serializers.IntegerField()
    overdue = serializers.IntegerField()


class WorklistSerializer(serializers.Serializer):
    awaiting_collection = LabRequestSerializer(many=True)
    in_laboratory = LabRequestSerializer(many=True)
    in_transit = SampleSerializer(many=True)
    rejections = SampleSerializer(many=True)
    counts = CountsSerializer()
    may_collect = serializers.BooleanField()
    may_receive = serializers.BooleanField()


class CollectInputSerializer(serializers.Serializer):
    requests = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    note = serializers.CharField(required=False, allow_blank=True, default="")
    #: The accession number of the tube this draw replaces, where the nurse
    #: is redrawing after a rejection. Optional: a redraw with the link left
    #: off is still a valid sample, and refusing it would mean refusing blood
    #: that has already been taken.
    replaces = serializers.UUIDField(required=False, allow_null=True)


class CollectedSerializer(serializers.Serializer):
    samples = SampleSerializer(many=True)


class RejectInputSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=RejectionReason.choices)
    note = serializers.CharField(required=False, allow_blank=True, default="")


class CancelInputSerializer(serializers.Serializer):
    reason = serializers.CharField()


class WorklistMessageSerializer(serializers.Serializer):
    detail = serializers.CharField()


# --------------------------------------------------------------------------
# Reading the worklist
# --------------------------------------------------------------------------


class WorklistView(CuraviaAPIView):
    """
    Everything the laboratory screen shows, in one request.

    One endpoint rather than four, because the four lists are read together
    and a screen that fetched them separately would show a sample as both in
    transit and already received for as long as the slowest request took.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = READ

    @extend_schema(operation_id="lab_worklist", responses={200: WorklistSerializer})
    def get(self, request: CuraviaRequest) -> Response:
        branch_id = current_branch_id()
        now = timezone.now()

        waiting = sorted(worklist.awaiting_collection(branch_id), key=worklist.sort_key)
        working = sorted(worklist.in_laboratory(branch_id), key=worklist.sort_key)

        return Response(
            {
                "awaiting_collection": LabRequestSerializer(waiting, many=True).data,
                "in_laboratory": LabRequestSerializer(working, many=True).data,
                "in_transit": SampleSerializer(
                    worklist.samples_in_transit(branch_id), many=True
                ).data,
                "rejections": SampleSerializer(
                    worklist.rejections(branch_id, limit=25), many=True
                ).data,
                "counts": worklist.counts(branch_id, now=now),
                "may_collect": _holds(request, COLLECT_CODE),
                "may_receive": _holds(request, RECEIVE_CODE),
            }
        )


class CollectionPlanView(CuraviaAPIView):
    """
    How many tubes to draw for one patient, and of what.

    Keyed on the patient rather than on a list of requests: the nurse is
    standing in front of a person, and what they need to know is how many
    times that person is about to be stuck.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = [COLLECT_CODE]

    @extend_schema(operation_id="lab_collection_plan", responses={200: TubeSerializer(many=True)})
    def get(self, request: CuraviaRequest, patient_id: str) -> Response:
        requests = [
            item
            for item in worklist.awaiting_collection(current_branch_id())
            if str(item.patient_id) == str(patient_id)
        ]
        tubes = worklist.plan_collection(requests)
        return Response([tube.as_dict() for tube in tubes])


# --------------------------------------------------------------------------
# Acting on it
# --------------------------------------------------------------------------


class CollectView(CuraviaAPIView):
    """
    Draw the tubes. Allocates the accession numbers and returns the samples.

    The caller then asks for the labels. Two steps rather than one response
    carrying a PDF, because a nurse whose printer is jammed must still have
    recorded that the blood was taken — and because labels get reprinted.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = [COLLECT_CODE]

    @extend_schema(
        operation_id="lab_collect",
        request=CollectInputSerializer,
        responses={201: CollectedSerializer, 400: WorklistMessageSerializer},
    )
    def post(self, request: CuraviaRequest) -> Response:
        form = CollectInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data

        requests = list(
            LabRequest.objects.filter(
                pk__in=data["requests"], branch_id=current_branch_id()
            ).select_related("panel__service", "patient")
        )
        if len(requests) != len(set(data["requests"])):
            return Response(
                {"detail": "One of those tests is not on this branch's worklist."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        replaces = None
        if data.get("replaces"):
            replaces = get_object_or_404(Sample, pk=data["replaces"])

        try:
            samples = worklist.collect(
                requests=requests,
                actor=request.user,
                note=data.get("note", ""),
                replaces=replaces,
            )
        except worklist.WorklistError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"samples": SampleSerializer(samples, many=True).data},
            status=status.HTTP_201_CREATED,
        )


class SampleReceiveView(CuraviaAPIView):
    """Book one tube in at the laboratory."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = [RECEIVE_CODE]

    @extend_schema(
        operation_id="lab_sample_receive",
        request=None,
        responses={200: SampleSerializer, 400: WorklistMessageSerializer},
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        sample = get_object_or_404(Sample.objects.select_related("patient"), pk=pk)
        try:
            worklist.receive(sample=sample, actor=request.user)
        except worklist.WorklistError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SampleSerializer(sample).data)


class SampleRejectView(CuraviaAPIView):
    """Refuse one tube, with a reason, and send its tests back to be redrawn."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = [RECEIVE_CODE]

    @extend_schema(
        operation_id="lab_sample_reject",
        request=RejectInputSerializer,
        responses={200: SampleSerializer, 400: WorklistMessageSerializer},
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        form = RejectInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        sample = get_object_or_404(Sample.objects.select_related("patient"), pk=pk)
        try:
            worklist.reject(
                sample=sample,
                reason=form.validated_data["reason"],
                note=form.validated_data.get("note", ""),
                actor=request.user,
            )
        except worklist.WorklistError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SampleSerializer(sample).data)


class RequestCancelView(CuraviaAPIView):
    """Take one test off the laboratory's list, before the bench has it."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = [COLLECT_CODE]

    @extend_schema(
        operation_id="lab_request_cancel",
        request=CancelInputSerializer,
        responses={200: LabRequestSerializer, 400: WorklistMessageSerializer},
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        form = CancelInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        lab_request = get_object_or_404(
            LabRequest.objects.select_related("panel__service", "patient"), pk=pk
        )
        try:
            worklist.cancel_request(
                request=lab_request, reason=form.validated_data["reason"], actor=request.user
            )
        except worklist.WorklistError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(LabRequestSerializer(lab_request).data)


# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------


class SampleLabelView(CuraviaAPIView):
    """
    The label for one tube, as a PDF.

    A GET, so the browser can open it in a tab and the print dialogue is the
    one the operating system shows. Reprinting is allowed and counted — see
    ``Sample.label_print_count``.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = [COLLECT_CODE]
    renderer_classes = [PdfRenderer]

    @extend_schema(operation_id="lab_sample_label", responses={(200, "application/pdf"): bytes})
    def get(self, request: CuraviaRequest, pk: str) -> HttpResponse:
        sample = get_object_or_404(Sample.objects.select_related("patient"), pk=pk)
        return _label_response(request, [sample])


class SampleLabelSheetView(CuraviaAPIView):
    """
    Labels for several tubes in one PDF: what a collection round prints.

    Takes the accession numbers rather than row ids in the query string so
    the address is one a scientist can read and check against the tubes in
    their hand.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = [COLLECT_CODE]
    renderer_classes = [PdfRenderer]

    @extend_schema(operation_id="lab_label_sheet", responses={(200, "application/pdf"): bytes})
    def get(self, request: CuraviaRequest) -> HttpResponse:
        wanted = [
            value.strip()
            for value in request.query_params.get("accessions", "").split(",")
            if value.strip()
        ]
        samples = list(
            Sample.objects.filter(accession__in=wanted, branch_id=current_branch_id())
            .select_related("patient")
            .order_by("accession")
        )
        if not samples:
            return HttpResponse(
                "No samples matched those numbers.", status=404, content_type="text/plain"
            )
        return _label_response(request, samples)


def _label_response(request: CuraviaRequest, samples: list[Sample]) -> HttpResponse:
    """
    Render, count the print, and audit it.

    Audited as a read of patient data, because that is what it is: a label
    carries a name, an MRN and a date of birth, and a PDF of fifty of them
    is a list of fifty patients leaving the system on paper.
    """
    organisation = request.organisation
    pdf = label_render.render_pdf(samples, hospital=organisation.name if organisation else "")
    label_render.mark_printed(samples, actor=request.user)

    for sample in samples:
        audit.record(
            "lab.label.printed",
            actor=request.user,
            object_type="laboratory.Sample",
            object_id=sample.pk,
            patient_id=sample.patient_id,
            changes={"accession": sample.accession, "copy": sample.label_print_count},
            **as_audit_kwargs(),
        )

    response = HttpResponse(pdf, content_type="application/pdf")
    name = samples[0].accession if len(samples) == 1 else "labels"
    response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
    # Patient-identifying paper. Never cached by a proxy or a shared browser.
    response["Cache-Control"] = "no-store, private"
    return response


__all__ = [
    "CollectView",
    "CollectionPlanView",
    "RequestCancelView",
    "RequestStatus",
    "SampleLabelSheetView",
    "SampleLabelView",
    "SampleReceiveView",
    "SampleRejectView",
    "SampleStatus",
    "WorklistView",
]
