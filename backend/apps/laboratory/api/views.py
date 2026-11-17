"""
The laboratory catalogue over HTTP.

Reading it and editing it are different permissions. Anyone who can see a
laboratory order can read the catalogue — a doctor looking at a result needs
to know the unit and the band it was judged against, and a result without
them is a number with no meaning. Editing is ``lab.catalogue.manage``, which
the role templates give to the owner and the laboratory supervisor only,
because a wrong reference range is a wrong flag on every result afterwards.

``required_permissions`` gates a whole route, so where one route both reads
and writes the write permission is checked inside the method against
``request.tenant_permissions`` — the same pattern as ``staff.invite`` in
``apps.accounts.api.staff_views``.

**The preview endpoint earns its place.** A supervisor editing bands for a
child cannot tell from a list of rows which one a six-year-old girl will
actually be judged against — that is what the overlap rule and the
specificity rule decide between them, and it is exactly the thing that is
easy to get wrong and invisible until a result is misflagged.
``/lab/analytes/<id>/preview/`` answers it directly: give it an age and a
sex, and it says which range applies and what a value would be flagged as.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.laboratory import catalogue, ranges
from apps.laboratory.models import Analyte, LabPanel, ReferenceRange

READ = ["lab.order.view"]
WRITE_CODE = "lab.catalogue.manage"
WRITE = [WRITE_CODE]

DENIED = {"detail": "Your role does not allow this.", "code": "permission_denied"}


def _may_edit(request: CuraviaRequest) -> bool:
    return WRITE_CODE in getattr(request, "tenant_permissions", frozenset())


def _changes(data: dict[str, Any]) -> dict[str, Any]:
    """
    What was edited, in a shape the audit log can store.

    ``AuditEvent.changes`` is a JSON column and reference ranges are
    Decimals, which json refuses. Written out as strings rather than floats:
    the audit log is a record of what somebody typed, and 0.1 must read back
    as 0.1.
    """
    return {
        name: (str(value) if isinstance(value, Decimal) else value) for name, value in data.items()
    }


# --------------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------------


class ReferenceRangeSerializer(serializers.ModelSerializer):
    sex_label = serializers.CharField(source="get_sex_display", read_only=True)
    #: "Women, 12 years and over" — the heading the screen and the printed
    #: report both use, built in one place so they cannot disagree.
    band = serializers.CharField(source="band_label", read_only=True)
    normal = serializers.CharField(source="normal_label", read_only=True)
    #: "below 7 or above 20". Formatted on the server for the same reason
    #: as the band: a threshold must read the same everywhere it appears.
    critical = serializers.CharField(source="critical_label", read_only=True)

    class Meta:
        model = ReferenceRange
        fields = (
            "id",
            "analyte",
            "sex",
            "sex_label",
            "age_min_days",
            "age_max_days",
            "low",
            "high",
            "critical_low",
            "critical_high",
            "note",
            "band",
            "normal",
            "critical",
            "from_template",
        )
        read_only_fields = (
            "id",
            "analyte",
            "sex_label",
            "band",
            "normal",
            "critical",
            "from_template",
        )


class AnalyteSerializer(serializers.ModelSerializer):
    value_type_label = serializers.CharField(source="get_value_type_display", read_only=True)
    reference_ranges = ReferenceRangeSerializer(source="ranges", many=True, read_only=True)

    class Meta:
        model = Analyte
        fields = (
            "id",
            "panel",
            "code",
            "name",
            "value_type",
            "value_type_label",
            "unit",
            "decimal_places",
            "options",
            "loinc_code",
            "sort_order",
            "is_active",
            "from_template",
            "reference_ranges",
        )
        read_only_fields = (
            "id",
            "panel",
            "value_type_label",
            "from_template",
            "reference_ranges",
        )


class PanelSerializer(serializers.ModelSerializer):
    code = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    specimen_label = serializers.CharField(source="get_specimen_display", read_only=True)
    analytes = AnalyteSerializer(many=True, read_only=True)

    class Meta:
        model = LabPanel
        fields = (
            "id",
            "service",
            "code",
            "name",
            "specimen",
            "specimen_label",
            "container",
            "volume_ml",
            "fasting_required",
            "preparation",
            "handling",
            "turnaround_minutes",
            "is_send_out",
            "is_active",
            "from_template",
            "analytes",
        )
        read_only_fields = ("id", "service", "code", "name", "specimen_label", "from_template")


class PanelSummarySerializer(serializers.ModelSerializer):
    """The list view: enough to choose a panel, without every range."""

    code = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    specimen_label = serializers.CharField(source="get_specimen_display", read_only=True)
    analyte_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = LabPanel
        fields = (
            "id",
            "service",
            "code",
            "name",
            "specimen",
            "specimen_label",
            "container",
            "turnaround_minutes",
            "fasting_required",
            "is_send_out",
            "is_active",
            "from_template",
            "analyte_count",
        )
        read_only_fields = fields


class PanelListSerializer(serializers.Serializer):
    panels = PanelSummarySerializer(many=True, read_only=True)
    #: How many ranges still carry Curavia's own starting values. The screen
    #: shows it as a nudge to the supervisor, not as a problem.
    unreviewed_ranges = serializers.IntegerField(read_only=True)
    may_edit = serializers.BooleanField(read_only=True)


class PreviewInputSerializer(serializers.Serializer):
    age_days = serializers.IntegerField(required=False, min_value=0, allow_null=True)
    sex = serializers.CharField(required=False, allow_blank=True)
    value = serializers.CharField(required=False, allow_blank=True)


class PreviewSerializer(serializers.Serializer):
    applies = ReferenceRangeSerializer(allow_null=True, read_only=True)
    describes = serializers.CharField(read_only=True)
    level = serializers.CharField(read_only=True, allow_blank=True)
    marker = serializers.CharField(read_only=True, allow_blank=True)


class MessageSerializer(serializers.Serializer):
    detail = serializers.CharField(read_only=True)


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------


class PanelListView(CuraviaAPIView):
    """Every laboratory panel this hospital has set up."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = READ

    @extend_schema(operation_id="lab_panels_list", responses={200: PanelListSerializer})
    def get(self, request: CuraviaRequest) -> Response:
        panels = list(
            LabPanel.objects.select_related("service")
            .prefetch_related("analytes")
            .order_by("service__name")
        )
        for panel in panels:
            # Counted off the prefetch rather than with an annotation, so the
            # list costs the same two queries whatever the hospital's size.
            panel.analyte_count = len(  # type: ignore[attr-defined]
                [a for a in panel.analytes.all() if a.is_active]
            )
        # The route is behind IsAuthenticated and the tenant middleware, so
        # there is always an organisation here; the count is simply skipped
        # rather than asserted, since a wrong nudge is not worth a 500.
        organisation = request.organisation
        unreviewed = catalogue.unreviewed_count(organisation) if organisation else 0
        return Response(
            {
                "panels": PanelSummarySerializer(panels, many=True).data,
                "unreviewed_ranges": unreviewed,
                "may_edit": _may_edit(request),
            }
        )


class PanelDetailView(CuraviaAPIView):
    """One panel, with every analyte and every range on it."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = READ

    def _panel(self, pk: str) -> LabPanel:
        return get_object_or_404(
            LabPanel.objects.select_related("service").prefetch_related("analytes__ranges"),
            pk=pk,
        )

    @extend_schema(operation_id="lab_panel_read", responses={200: PanelSerializer})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        return Response(PanelSerializer(self._panel(pk)).data)

    @extend_schema(
        request=PanelSerializer, responses={200: PanelSerializer, 403: MessageSerializer}
    )
    def patch(self, request: CuraviaRequest, pk: str) -> Response:
        if not _may_edit(request):
            return Response(DENIED, status=status.HTTP_403_FORBIDDEN)
        panel = self._panel(pk)
        serializer = PanelSerializer(panel, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save(from_template=False)
        audit.record(
            "lab.panel.change",
            actor=request.user,
            object_type="LabPanel",
            object_id=updated.pk,
            changes=_changes(serializer.validated_data),
            **as_audit_kwargs(),
        )
        return Response(PanelSerializer(updated).data)


# --------------------------------------------------------------------------
# Analytes
# --------------------------------------------------------------------------


class AnalyteListView(CuraviaAPIView):
    """Add an analyte to a panel."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = WRITE

    @extend_schema(
        request=AnalyteSerializer,
        responses={201: AnalyteSerializer, 400: MessageSerializer},
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        panel = get_object_or_404(LabPanel.objects.select_related("service"), pk=pk)
        serializer = AnalyteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        analyte = Analyte(organisation=panel.organisation, panel=panel, **serializer.validated_data)
        try:
            catalogue.save_analyte(analyte)
        except catalogue.CatalogueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        audit.record(
            "lab.analyte.add",
            actor=request.user,
            object_type="Analyte",
            object_id=analyte.pk,
            changes={"panel": panel.code, "code": analyte.code, "name": analyte.name},
            **as_audit_kwargs(),
        )
        return Response(AnalyteSerializer(analyte).data, status=status.HTTP_201_CREATED)


class AnalyteDetailView(CuraviaAPIView):
    """Edit one analyte."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = WRITE

    @extend_schema(
        request=AnalyteSerializer,
        responses={200: AnalyteSerializer, 400: MessageSerializer},
    )
    def patch(self, request: CuraviaRequest, pk: str) -> Response:
        analyte = get_object_or_404(Analyte.objects.select_related("panel__service"), pk=pk)
        serializer = AnalyteSerializer(analyte, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for name, value in serializer.validated_data.items():
            setattr(analyte, name, value)
        try:
            catalogue.save_analyte(analyte)
        except catalogue.CatalogueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        audit.record(
            "lab.analyte.change",
            actor=request.user,
            object_type="Analyte",
            object_id=analyte.pk,
            changes=_changes(serializer.validated_data),
            **as_audit_kwargs(),
        )
        return Response(AnalyteSerializer(analyte).data)


class AnalytePreviewView(CuraviaAPIView):
    """
    Which range applies to a patient of this age and sex, and what a value
    would be flagged as. See the module docstring.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = READ

    @extend_schema(request=PreviewInputSerializer, responses={200: PreviewSerializer})
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        analyte = get_object_or_404(Analyte.objects.prefetch_related("ranges"), pk=pk)
        form = PreviewInputSerializer(data=request.data)
        form.is_valid(raise_exception=True)

        age_days = form.validated_data.get("age_days")
        sex = form.validated_data.get("sex") or None
        loaded = list(analyte.ranges.all())

        applies = ranges.range_for(analyte, sex=sex, age_days=age_days, ranges=loaded)
        payload: dict[str, Any] = {
            "applies": ReferenceRangeSerializer(applies).data if applies else None,
            "describes": ranges.describe(analyte, sex=sex, age_days=age_days, ranges=loaded),
            "level": "",
            "marker": "",
        }

        raw = form.validated_data.get("value", "")
        if raw != "":
            judged = ranges.flag(analyte, raw, sex=sex, age_days=age_days, ranges=loaded)
            payload["level"] = str(judged.level)
            payload["marker"] = judged.marker
        return Response(payload)


# --------------------------------------------------------------------------
# Reference ranges
# --------------------------------------------------------------------------


class RangeListView(CuraviaAPIView):
    """Add a reference range to an analyte."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = WRITE

    @extend_schema(
        request=ReferenceRangeSerializer,
        responses={201: ReferenceRangeSerializer, 400: MessageSerializer},
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        analyte = get_object_or_404(Analyte.objects.all(), pk=pk)
        serializer = ReferenceRangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        band = ReferenceRange(
            organisation=analyte.organisation, analyte=analyte, **serializer.validated_data
        )
        try:
            catalogue.save_range(band)
        except catalogue.CatalogueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        audit.record(
            "lab.range.add",
            actor=request.user,
            object_type="ReferenceRange",
            object_id=band.pk,
            changes={"analyte": analyte.code, "band": band.band_label()},
            **as_audit_kwargs(),
        )
        return Response(ReferenceRangeSerializer(band).data, status=status.HTTP_201_CREATED)


class RangeDetailView(CuraviaAPIView):
    """Edit or remove one reference range."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = WRITE

    def _range(self, pk: str) -> ReferenceRange:
        return get_object_or_404(ReferenceRange.objects.select_related("analyte"), pk=pk)

    @extend_schema(
        request=ReferenceRangeSerializer,
        responses={200: ReferenceRangeSerializer, 400: MessageSerializer},
    )
    def patch(self, request: CuraviaRequest, pk: str) -> Response:
        band = self._range(pk)
        serializer = ReferenceRangeSerializer(band, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for name, value in serializer.validated_data.items():
            setattr(band, name, value)
        try:
            catalogue.save_range(band)
        except catalogue.CatalogueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

        audit.record(
            "lab.range.change",
            actor=request.user,
            object_type="ReferenceRange",
            object_id=band.pk,
            changes=_changes(serializer.validated_data),
            **as_audit_kwargs(),
        )
        return Response(ReferenceRangeSerializer(band).data)

    @extend_schema(responses={204: None})
    def delete(self, request: CuraviaRequest, pk: str) -> Response:
        band = self._range(pk)
        audit.record(
            "lab.range.remove",
            actor=request.user,
            object_type="ReferenceRange",
            object_id=band.pk,
            changes={"analyte": band.analyte.code, "band": band.band_label()},
            **as_audit_kwargs(),
        )
        band.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
