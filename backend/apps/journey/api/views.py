"""
The journey over HTTP.

Everything the board does live is also available as a plain request, and that
is not redundancy — it is what makes the live part safe to rely on. A socket
that drops, a proxy that buffers, a laptop that slept: in each case the board
re-fetches here and is correct again. A feature whose only path is a
WebSocket is a feature that silently shows stale patient data, and a stale
board is worse than no board, because people believe it.
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
from apps.core.models import Branch, Department
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.core.tenancy import tenant_scope, unscoped
from apps.core.ws_tickets import issue_ticket
from apps.journey import service
from apps.journey.models import DisplayScreen, Encounter, JourneyEvent, Stage
from apps.patients.models import Patient


class StartSerializer(serializers.Serializer):
    patient_id = serializers.UUIDField()
    department_id = serializers.UUIDField(required=False, allow_null=True)
    kind = serializers.ChoiceField(
        choices=Encounter.Kind.choices, required=False, default=Encounter.Kind.OUTPATIENT
    )
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True)


class MoveSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=Stage.choices)
    department_id = serializers.UUIDField(required=False, allow_null=True)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True)


class EventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()
    to_stage_label = serializers.SerializerMethodField()
    department_name = serializers.CharField(source="department.name", read_only=True, default="")

    class Meta:
        model = JourneyEvent
        fields = (
            "id",
            "at",
            "from_stage",
            "to_stage",
            "to_stage_label",
            "department_name",
            "actor_name",
            "minutes_in_previous_stage",
            "previous_was_waiting",
            "note",
        )

    def get_actor_name(self, obj: JourneyEvent) -> str:
        user = obj.actor
        return (user.get_full_name() or user.email) if user else ""

    def get_to_stage_label(self, obj: JourneyEvent) -> str:
        # str() because the labels are lazily translated, and a lazy proxy
        # serialises as a repr rather than as the sentence it stands for.
        return str(dict(Stage.choices).get(obj.to_stage, obj.to_stage))


class DisplayScreenSerializer(serializers.ModelSerializer):
    department_names = serializers.SerializerMethodField()
    #: The URL to type into the television once. Returned on create and on a
    #: deliberate re-read, never in a list — see DisplayScreenListView.
    url = serializers.SerializerMethodField()

    class Meta:
        model = DisplayScreen
        fields = ("id", "name", "footer", "is_active", "last_seen_at", "department_names", "url")

    def get_department_names(self, obj: DisplayScreen) -> list[str]:
        return list(obj.departments.values_list("name", flat=True))

    def get_url(self, obj: DisplayScreen) -> str:
        if not self.context.get("with_token"):
            return ""
        return f"/display/{obj.token}"


class WebSocketTicketView(CuraviaAPIView):
    """
    A short-lived ticket for opening a socket.

    The staff app holds JWTs and has no Django session, which is what
    Channels authenticates from — see apps/core/ws_tickets.py for why this
    exists and what the alternatives were.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "auth"

    @extend_schema(
        request=None,
        responses={200: OpenApiResponse(description="A single-use WebSocket ticket")},
    )
    def post(self, request: CuraviaRequest) -> Response:
        ticket, seconds = issue_ticket(request.user)
        return Response({"ticket": ticket, "expires_in": seconds})


class BoardView(CuraviaAPIView):
    """
    The board as a plain request.

    Audited as a bulk read: it is every patient in the building, with their
    name, their number and which department has them.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["journey.view"]

    @extend_schema(responses={200: OpenApiResponse(description="Everybody in the building")})
    def get(self, request: CuraviaRequest) -> Response:
        branch = _active_branch(request)
        if branch is None:
            return Response(
                {"detail": "Which branch?", "code": "branch_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rows = service.board_rows(branch.pk)
        if rows:
            audit.record(
                "journey.board.view",
                actor=request.user,
                changes={"branch": branch.name, "patients": len(rows)},
                flags=[audit.AuditFlag.BULK],
                **as_audit_kwargs(),
            )
        return Response({"branch": branch.name, "rows": rows})


class StartEncounterView(CuraviaAPIView):
    """Open a visit."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["queue.manage"]

    @extend_schema(request=StartSerializer, responses={201: OpenApiResponse(description="Open")})
    def post(self, request: CuraviaRequest) -> Response:
        serializer = StartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        patient = Patient.objects.filter(pk=data["patient_id"]).first()
        branch = _active_branch(request)
        if patient is None or branch is None:
            return Response(
                {"detail": "That patient or branch was not found.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        department = (
            Department.objects.filter(pk=data["department_id"]).first()
            if data.get("department_id")
            else None
        )

        try:
            encounter = service.start_encounter(
                patient=patient,
                branch=branch,
                department=department,
                kind=data.get("kind", Encounter.Kind.OUTPATIENT),
                reason=data.get("reason", ""),
                actor=request.user,
            )
        except service.JourneyError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_start"},
                status=status.HTTP_409_CONFLICT,
            )

        audit.record(
            "journey.start",
            actor=request.user,
            object_type="Encounter",
            object_id=encounter.pk,
            patient_id=patient.pk,
            changes={"kind": encounter.kind},
            **as_audit_kwargs(),
        )
        return Response(
            {"encounter": service.board_rows(branch.pk) and _one(encounter)},
            status=status.HTTP_201_CREATED,
        )


class MoveView(CuraviaAPIView):
    """
    Send a patient to the next stage.

    ``queue.manage`` rather than a permission of its own: moving somebody
    along the queue is the same act whether it is the front desk sending
    them to triage or the nurse sending them to the doctor, and inventing a
    second code for it would mean every hospital configuring the same thing
    twice.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["queue.manage"]

    @extend_schema(
        request=MoveSerializer,
        responses={200: OpenApiResponse(description="Moved")},
        operation_id="journey_move_encounter",
    )
    def post(self, request: CuraviaRequest, pk: str) -> Response:
        serializer = MoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        encounter = Encounter.objects.filter(pk=pk).first()
        if encounter is None:
            return Response(
                {"detail": "No such visit.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        department = (
            Department.objects.filter(pk=data["department_id"]).first()
            if data.get("department_id")
            else None
        )

        previous = encounter.stage
        try:
            encounter = service.move_to(
                encounter,
                data["stage"],
                department=department,
                actor=request.user,
                note=data.get("note", ""),
            )
        except service.JourneyError as exc:
            return Response(
                {"detail": str(exc), "code": "cannot_move"},
                status=status.HTTP_409_CONFLICT,
            )

        audit.record(
            "journey.move",
            actor=request.user,
            object_type="Encounter",
            object_id=encounter.pk,
            patient_id=encounter.patient_id,
            changes={"from": previous, "to": encounter.stage},
            **as_audit_kwargs(),
        )
        return Response(_one(encounter))


class EncounterEventsView(CuraviaAPIView):
    """
    One visit's journey, with how long each part took.

    This is the screen somebody opens when a patient complains about the
    wait, which is why the minutes are stored rather than recomputed.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["journey.view"]

    @extend_schema(responses={200: EventSerializer(many=True)})
    def get(self, request: CuraviaRequest, pk: str) -> Response:
        encounter = Encounter.objects.filter(pk=pk).select_related("patient").first()
        if encounter is None:
            return Response(
                {"detail": "No such visit.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        events = encounter.events.select_related("actor", "department").order_by("at")
        waiting = sum(
            event.minutes_in_previous_stage for event in events if event.previous_was_waiting
        )
        audit.record_view(
            actor=request.user,
            object_type="Encounter",
            object_id=encounter.pk,
            patient_id=encounter.patient_id,
            fields=["journey"],
            **as_audit_kwargs(),
        )
        return Response(
            {
                "encounter": _one(encounter),
                "events": EventSerializer(events, many=True).data,
                "minutes_waiting": waiting,
            }
        )


class DisplayScreenListView(CuraviaAPIView):
    """
    The televisions at this branch.

    The list does **not** carry the tokens. A screen's URL is a credential
    that needs no password, so it is shown once when the screen is created
    and after a deliberate re-read — not on every load of a settings page
    that somebody might have open on a shared machine.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["org.branch.manage"]

    @extend_schema(responses={200: DisplayScreenSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        screens = DisplayScreen.objects.all().prefetch_related("departments")
        return Response(DisplayScreenSerializer(screens, many=True).data)

    @extend_schema(request=None, responses={201: DisplayScreenSerializer})
    def post(self, request: CuraviaRequest) -> Response:
        body: Any = request.data if hasattr(request.data, "get") else {}
        branch = _active_branch(request)
        if branch is None:
            return Response(
                {"detail": "Which branch?", "code": "branch_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        name = str(body.get("name", "")).strip()
        if not name:
            return Response(
                {"detail": "Give the screen a name — where it is.", "code": "name_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        screen = DisplayScreen.objects.create(
            organisation_id=request.organisation.pk,  # type: ignore[union-attr]
            branch=branch,
            name=name,
            footer=str(body.get("footer", ""))[:200],
        )
        if departments := body.get("department_ids") or []:
            screen.departments.set(Department.objects.filter(pk__in=departments))

        audit.record(
            "display.create",
            actor=request.user,
            object_type="DisplayScreen",
            object_id=screen.pk,
            changes={"name": screen.name},
            **as_audit_kwargs(),
        )
        return Response(
            DisplayScreenSerializer(screen, context={"with_token": True}).data,
            status=status.HTTP_201_CREATED,
        )


class DisplayScreenTokenView(CuraviaAPIView):
    """Re-read or rotate one screen's URL. Both are audited."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["org.branch.manage"]

    @extend_schema(
        request=None,
        responses={200: DisplayScreenSerializer},
        operation_id="display_screen_reveal_or_rotate",
    )
    def post(self, request: CuraviaRequest, pk: str, action: str) -> Response:
        if action not in {"reveal", "rotate"}:
            return Response(
                {"detail": f"There is nothing called {action}.", "code": "unknown_action"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        screen = DisplayScreen.objects.filter(pk=pk).first()
        if screen is None:
            return Response(
                {"detail": "No such screen.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if action == "rotate":
            screen.rotate_token()

        audit.record(
            f"display.{action}",
            actor=request.user,
            object_type="DisplayScreen",
            object_id=screen.pk,
            changes={"name": screen.name},
            **as_audit_kwargs(),
        )
        return Response(DisplayScreenSerializer(screen, context={"with_token": True}).data)


class PublicDisplayView(CuraviaAPIView):
    """
    What one television shows, for a client that has no socket.

    Unauthenticated, because a television has no account. The token in the
    URL is the whole of the authorisation, and what it buys is a payload of
    numbers with no names in it — built by ``service.display_rows``, which
    is the only function that builds it.
    """

    permission_classes = [AllowAny]
    authentication_classes: list[Any] = []
    throttle_scope = "search"

    @extend_schema(
        parameters=[OpenApiParameter("token", str, location="path")],
        responses={200: OpenApiResponse(description="Numbers being called")},
    )
    def get(self, request: CuraviaRequest, token: str) -> Response:
        with unscoped(reason="display screen: a television has no session, only its token"):
            screen = DisplayScreen.unscoped.filter(token=token, is_active=True).first()
        if screen is None:
            return Response(
                {"detail": "That screen is not set up.", "code": "not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        with tenant_scope(screen.organisation_id, screen.branch_id):
            screen = DisplayScreen.objects.get(pk=screen.pk)
            return Response(service.display_rows(screen))


# ---------------------------------------------------------------------------


def _one(encounter: Encounter) -> dict[str, Any]:
    from django.utils import timezone

    fresh = (
        Encounter.objects.select_related("patient", "department", "ticket", "attending__user")
        .filter(pk=encounter.pk)
        .first()
    )
    return service._row(fresh or encounter, timezone.now())


def _active_branch(request: CuraviaRequest) -> Branch | None:
    from apps.core.tenancy import current_branch_id

    active = current_branch_id()
    if active:
        return Branch.objects.filter(pk=active).first()
    return Branch.objects.filter(is_main=True).first()


__all__ = [
    "BoardView",
    "DisplayScreenListView",
    "DisplayScreenTokenView",
    "EncounterEventsView",
    "MoveView",
    "PublicDisplayView",
    "StartEncounterView",
    "WebSocketTicketView",
]
