"""
Orders and referrals over HTTP.

Writing an order needs ``clinical.order.create`` and a referral needs
``clinical.referral.create`` — both already in the permissions matrix, held
by doctors and consultants. Reading them needs ``clinical.note.read``,
because a nurse giving a drug and a pharmacist checking one both have to see
what was ordered without being able to order anything themselves.

Signing the basket is its own route rather than a flag on the create call.
It is a different act: creating a draft is thinking, and signing is
instructing a department to do something to a patient.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.catalogue.models import Service
from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.models import Department
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.journey.models import Encounter
from apps.orders import service as ordering
from apps.orders.models import Order, OrderKind, OrderPriority, Referral


class OrderSerializer(serializers.ModelSerializer):
    ordered_by_name = serializers.SerializerMethodField()
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    priority_label = serializers.CharField(source="get_priority_display", read_only=True)

    class Meta:
        model = Order
        fields = (
            "id",
            "kind",
            "kind_label",
            "service",
            "name",
            "summary",
            "instructions",
            "reason",
            "priority",
            "priority_label",
            "status",
            "status_label",
            "dose",
            "route",
            "frequency",
            "duration",
            "quantity",
            "estimate_kobo",
            "estimate_note",
            "warnings",
            "override_reason",
            "ordered_at",
            "ordered_by_name",
            "cancel_reason",
            "created_at",
        )

    def get_ordered_by_name(self, obj: Order) -> str:
        user = obj.ordered_by
        return (user.get_full_name() or user.email) if user else ""


class ReferralSerializer(serializers.ModelSerializer):
    referred_by_name = serializers.SerializerMethodField()
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    urgency_label = serializers.CharField(source="get_urgency_display", read_only=True)
    department_name = serializers.CharField(source="to_department.name", read_only=True)

    class Meta:
        model = Referral
        fields = (
            "id",
            "kind",
            "kind_label",
            "status",
            "status_label",
            "urgency",
            "urgency_label",
            "to_department",
            "department_name",
            "to_facility",
            "to_clinician",
            "speciality",
            "destination",
            "reason",
            "letter",
            "referred_at",
            "referred_by_name",
            "responded_at",
            "response_note",
            "created_at",
        )

    def get_referred_by_name(self, obj: Referral) -> str:
        user = obj.referred_by
        return (user.get_full_name() or user.email) if user else ""


class OrderInputSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=OrderKind.choices)
    service_id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    priority = serializers.ChoiceField(
        choices=OrderPriority.choices, required=False, default=OrderPriority.ROUTINE
    )
    instructions = serializers.CharField(
        max_length=300, required=False, allow_blank=True, default=""
    )
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    dose = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    route = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    frequency = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    duration = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class ReferralInputSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=Referral.Kind.choices)
    reason = serializers.CharField(max_length=300)
    department_id = serializers.UUIDField(required=False, allow_null=True)
    to_facility = serializers.CharField(
        max_length=160, required=False, allow_blank=True, default=""
    )
    to_clinician = serializers.CharField(
        max_length=160, required=False, allow_blank=True, default=""
    )
    speciality = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    urgency = serializers.ChoiceField(
        choices=Referral.Urgency.choices, required=False, default=Referral.Urgency.ROUTINE
    )
    letter = serializers.CharField(required=False, allow_blank=True, default="")


def _not_found(what: str) -> Response:
    return Response({"detail": what, "code": "not_found"}, status=status.HTTP_404_NOT_FOUND)


def _refused(exc: Exception, code: str = "cannot_order") -> Response:
    return Response({"detail": str(exc), "code": code}, status=status.HTTP_400_BAD_REQUEST)


def _body(request: CuraviaRequest) -> Any:
    # `request.data` is a list for a JSON array body, which a client posting
    # the wrong shape would send.
    return request.data if hasattr(request.data, "get") else {}


#: Which part of the price list each kind of order comes from. A doctor
#: ordering a scan should not have to scroll past the ward's dressings.
CATEGORY_FOR_KIND: dict[str, tuple[str, ...]] = {
    OrderKind.LABORATORY: ("laboratory",),
    OrderKind.RADIOLOGY: ("radiology",),
    OrderKind.MEDICATION: ("pharmacy",),
    OrderKind.PROCEDURE: ("procedure", "theatre"),
    OrderKind.NURSING: ("nursing",),
}


class ServiceSearchView(CuraviaAPIView):
    """
    The hospital's own list, for the order being written.

    Each result carries what it costs here today, because the question a
    patient asks before agreeing to a test is how much — and a doctor who
    has to leave the screen to find out will stop asking.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.read"]
    throttle_scope = "search"

    @extend_schema(
        parameters=[
            OpenApiParameter("q", str, required=False),
            OpenApiParameter("kind", str, required=False),
        ],
        responses={200: OpenApiResponse(description="Matching services, with prices")},
    )
    def get(self, request: CuraviaRequest) -> Response:
        from apps.catalogue.pricing import PriceNotFound, resolve_price
        from apps.core.tenancy import current_branch_id

        query = request.query_params.get("q", "").strip()
        kind = request.query_params.get("kind", "")

        rows = Service.objects.filter(is_active=True)
        if categories := CATEGORY_FOR_KIND.get(kind):
            rows = rows.filter(category__in=categories)
        if query:
            rows = rows.filter(Q(name__icontains=query) | Q(code__icontains=query))

        branch_id = current_branch_id()
        results = []
        for item in rows.order_by("name")[:20]:
            try:
                price: int | None = resolve_price(
                    service_id=item.pk, branch_id=branch_id
                ).amount_kobo
            except PriceNotFound:
                price = None
            results.append(
                {
                    "id": str(item.pk),
                    "code": item.code,
                    "name": item.name,
                    "category": item.category,
                    "price_kobo": price,
                }
            )
        return Response({"results": results})


class EncounterOrdersView(CuraviaAPIView):
    """The orders on one visit, and adding to the basket."""

    permission_classes = [IsAuthenticated, HasPermission]

    def get_permissions(self):  # type: ignore[no-untyped-def]
        self.required_permissions = (
            ["clinical.order.create"] if self.request.method == "POST" else ["clinical.note.read"]
        )
        return super().get_permissions()

    @extend_schema(responses={200: OrderSerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        encounter = Encounter.objects.filter(pk=pk).first()
        if encounter is None:
            return _not_found("No such visit.")

        rows = ordering.for_encounter(encounter)
        drafts = [order for order in rows if order.is_draft]
        return Response(
            {
                "orders": OrderSerializer(rows, many=True).data,
                "basket_total_kobo": ordering.basket_total(drafts),
                "basket_count": len(drafts),
            }
        )

    @extend_schema(
        request=OrderInputSerializer,
        responses={201: OrderSerializer},
        operation_id="orders_add_to_basket",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = OrderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return _not_found("No such visit.")

        chosen = None
        if service_id := data.pop("service_id", None):
            chosen = Service.objects.filter(pk=service_id, is_active=True).first()
            if chosen is None:
                return _not_found("That service is not on this hospital's list.")

        try:
            order = ordering.draft(encounter=encounter, service=chosen, actor=request.user, **data)
        except ordering.OrderError as exc:
            return _refused(exc)
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError

            if isinstance(exc, DjangoValidationError):
                return Response(
                    {
                        "detail": "That order needs checking.",
                        "code": "invalid",
                        "fields": getattr(exc, "message_dict", {"detail": exc.messages}),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise

        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


class SignOrdersView(CuraviaAPIView):
    """
    Send the whole basket.

    Its own route because it is a different act from drafting: a draft is
    thinking, and this instructs a department to do something to a patient.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.order.create"]

    @extend_schema(
        request=serializers.Serializer,
        responses={200: OrderSerializer(many=True)},
        operation_id="orders_sign_basket",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return _not_found("No such visit.")

        override = str(_body(request).get("override_reason", ""))[:300]
        try:
            signed = ordering.sign(
                encounter=encounter, actor=request.user, override_reason=override
            )
        except ordering.OrderError as exc:
            return _refused(exc, code="cannot_sign")

        for order in signed:
            audit.record(
                "clinical.order.create",
                actor=request.user,
                object_type="Order",
                object_id=order.pk,
                patient_id=encounter.patient_id,
                changes={
                    "kind": order.kind,
                    "name": order.name,
                    "priority": order.priority,
                    "overridden": bool(order.override_reason),
                },
                flags=[audit.AuditFlag.SENSITIVE] if order.override_reason else [],
                **as_audit_kwargs(),
            )
        return Response({"orders": OrderSerializer(signed, many=True).data})


class OrderDetailView(CuraviaAPIView):
    """Change a draft, or stop an order."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.order.create"]

    @extend_schema(
        request=OrderInputSerializer,
        responses={200: OrderSerializer},
        operation_id="orders_update_draft",
    )
    def patch(self, request: CuraviaRequest, pk: str) -> Response:
        order = Order.objects.filter(pk=pk).select_related("encounter").first()
        if order is None:
            return _not_found("No such order.")

        body = _body(request)
        try:
            order = ordering.update_draft(
                order=order,
                actor=request.user,
                **{
                    field: body.get(field)
                    for field in (
                        "name",
                        "instructions",
                        "reason",
                        "priority",
                        "dose",
                        "route",
                        "frequency",
                        "duration",
                        "quantity",
                    )
                },
            )
        except ordering.OrderError as exc:
            return _refused(exc)

        return Response(OrderSerializer(order).data)

    @extend_schema(
        request=serializers.Serializer,
        responses={200: OrderSerializer},
        operation_id="orders_cancel",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        order = Order.objects.filter(pk=pk).first()
        if order is None:
            return _not_found("No such order.")

        reason = str(_body(request).get("reason", ""))
        try:
            order = ordering.cancel(order=order, reason=reason, actor=request.user)
        except ordering.OrderError as exc:
            return _refused(exc, code="cannot_cancel")

        audit.record(
            "clinical.order.cancel",
            actor=request.user,
            object_type="Order",
            object_id=order.pk,
            patient_id=order.patient_id,
            changes={"name": order.name, "reason": order.cancel_reason},
            **as_audit_kwargs(),
        )
        return Response(OrderSerializer(order).data)


class PatientOrdersView(CuraviaAPIView):
    """Everything ever ordered for one patient."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.note.read"]

    @extend_schema(responses={200: OrderSerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        from apps.patients.models import Patient

        patient = Patient.objects.filter(pk=pk).first()
        if patient is None:
            return _not_found("No such patient.")

        rows = ordering.for_patient(patient)
        audit.record_view(
            actor=request.user,
            object_type="Patient",
            object_id=patient.pk,
            patient_id=patient.pk,
            fields=["orders"],
            **as_audit_kwargs(),
        )
        return Response({"orders": OrderSerializer(rows, many=True).data})


class EncounterReferralsView(CuraviaAPIView):
    """Referrals made on one visit."""

    permission_classes = [IsAuthenticated, HasPermission]

    def get_permissions(self):  # type: ignore[no-untyped-def]
        self.required_permissions = (
            ["clinical.referral.create"]
            if self.request.method == "POST"
            else ["clinical.note.read"]
        )
        return super().get_permissions()

    @extend_schema(responses={200: ReferralSerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        encounter = Encounter.objects.filter(pk=pk).first()
        if encounter is None:
            return _not_found("No such visit.")
        return Response(
            {"referrals": ReferralSerializer(ordering.referrals_for(encounter), many=True).data}
        )

    @extend_schema(
        request=ReferralInputSerializer,
        responses={201: ReferralSerializer},
        operation_id="orders_refer",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = ReferralInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)

        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return _not_found("No such visit.")

        department = None
        if department_id := data.pop("department_id", None):
            department = Department.objects.filter(pk=department_id).first()
            if department is None:
                return _not_found("No such department.")

        try:
            referral = ordering.refer(
                encounter=encounter, to_department=department, actor=request.user, **data
            )
        except ordering.OrderError as exc:
            return _refused(exc, code="cannot_refer")
        except Exception as exc:
            from django.core.exceptions import ValidationError as DjangoValidationError

            if isinstance(exc, DjangoValidationError):
                return Response(
                    {
                        "detail": "That referral needs checking.",
                        "code": "invalid",
                        "fields": getattr(exc, "message_dict", {"detail": exc.messages}),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise

        audit.record(
            "clinical.referral.create",
            actor=request.user,
            object_type="Referral",
            object_id=referral.pk,
            patient_id=encounter.patient_id,
            changes={"kind": referral.kind, "to": referral.destination},
            **as_audit_kwargs(),
        )
        return Response(ReferralSerializer(referral).data, status=status.HTTP_201_CREATED)


class ReferralDetailView(CuraviaAPIView):
    """The other end answers."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["clinical.referral.create"]

    @extend_schema(
        request=serializers.Serializer,
        responses={200: ReferralSerializer},
        operation_id="orders_answer_referral",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        referral = Referral.objects.filter(pk=pk).first()
        if referral is None:
            return _not_found("No such referral.")

        body = _body(request)
        try:
            referral = ordering.respond_to_referral(
                referral=referral,
                status=str(body.get("status", "")),
                note=str(body.get("note", "")),
                actor=request.user,
            )
        except ordering.OrderError as exc:
            return _refused(exc, code="cannot_answer")

        audit.record(
            "clinical.referral.answer",
            actor=request.user,
            object_type="Referral",
            object_id=referral.pk,
            patient_id=referral.patient_id,
            changes={"status": referral.status},
            **as_audit_kwargs(),
        )
        return Response(ReferralSerializer(referral).data)


__all__ = [
    "EncounterOrdersView",
    "EncounterReferralsView",
    "OrderDetailView",
    "PatientOrdersView",
    "ReferralDetailView",
    "ServiceSearchView",
    "SignOrdersView",
]
