"""
The setup wizard's endpoints.

Two shapes, not fourteen:

    GET  /api/v1/onboarding/setup/          what the wizard needs to draw itself
    POST /api/v1/onboarding/setup/<step>/   apply one step
    POST /api/v1/onboarding/setup/<step>/skip/

The GET returns the steps, what is already done, and the templates on offer
for this hospital's size and modules — so the wizard makes one request and
has everything, including when the person goes backwards through it.

Who may do this: ``org.settings.change``, which in the seeded roles is the
owner and the facility administrator. It is explicitly not something a
practice manager with staff permissions can do, because these steps decide
what the hospital charges.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.catalogue.models import Payer, Service
from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.models import Bed, Branch, Department
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.core.setup_steps import (
    SETUP_STEPS,
    completed_steps,
    mark_step,
    next_step,
    setup_is_complete,
)
from apps.onboarding import setup
from apps.onboarding import templates_data as templates

#: step key -> the function that applies it, and the shape it expects.
#: "rows" means a list under `rows`; "object" means the payload itself.
APPLY: dict[str, tuple[Any, str]] = {
    "profile": (setup.apply_profile, "object"),
    "branches": (setup.apply_branches, "rows"),
    "departments": (setup.apply_departments, "branch_rows"),
    "wards": (setup.apply_wards, "branch_rows"),
    "services": (setup.apply_services, "rows"),
    "payers": (setup.apply_payers, "rows"),
    "staff": (setup.apply_staff, "rows"),
}


class SetupStateSerializer(serializers.Serializer):
    """Documented for the schema; built by hand in the view."""

    complete = serializers.BooleanField()
    next_step = serializers.CharField(allow_null=True)
    steps = serializers.ListField(child=serializers.DictField())
    templates = serializers.DictField()
    counts = serializers.DictField()


class StepResultSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    detail = serializers.CharField()
    complete = serializers.BooleanField()
    next_step = serializers.CharField(allow_null=True)


class SetupStateView(CuraviaAPIView):
    """Everything the wizard needs, in one request."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["org.settings.change"]

    @extend_schema(responses={200: SetupStateSerializer})
    def get(self, request: CuraviaRequest) -> Response:
        organisation = request.organisation
        if organisation is None:
            return Response(
                {"detail": "You are not signed in to a hospital.", "code": "no_organisation"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        done = completed_steps(organisation)
        modules = set(request.available_modules)
        size = organisation.size_mode

        return Response(
            {
                "complete": setup_is_complete(organisation),
                "next_step": next_step(organisation),
                "steps": [
                    {
                        "key": step.key,
                        "title": step.title,
                        "blurb": step.blurb,
                        "required": step.required,
                        "done": step.key in done,
                    }
                    for step in SETUP_STEPS
                ],
                "templates": {
                    "departments": [
                        {
                            "code": t.code,
                            "name": t.name,
                            "kind": t.kind,
                            "common": t.common,
                        }
                        for t in templates.departments_for(size)
                    ],
                    "services": [
                        {
                            "code": t.code,
                            "name": t.name,
                            "category": t.category,
                            "price_kobo": t.price_kobo,
                            "common": t.common,
                        }
                        for t in templates.services_for(size, modules)
                    ],
                    "payers": [
                        {
                            "code": t.code,
                            "name": t.name,
                            "kind": t.kind,
                            "common": t.common,
                        }
                        for t in templates.payers_for()
                        if t.kind != Payer.Kind.SELF
                    ],
                    "ward_beds": [
                        {"department_code": t.department_code, "beds": list(t.beds)}
                        for t in templates.WARD_BED_TEMPLATES
                    ],
                    "bed_kinds": [
                        {"value": choice.value, "label": choice.label} for choice in Bed.Kind
                    ],
                },
                # What the hospital already has, so the wizard can say "8
                # departments" rather than offering the list as if it were
                # the first time.
                "counts": {
                    "branches": Branch.objects.count(),
                    "departments": Department.objects.count(),
                    "wards": Department.objects.filter(kind=Department.Kind.WARD).count(),
                    "beds": Bed.objects.count(),
                    "services": Service.objects.count(),
                    "payers": Payer.objects.exclude(kind=Payer.Kind.SELF).count(),
                },
                "organisation": {
                    "name": organisation.name,
                    "size_mode": organisation.size_mode,
                    "cac_number": organisation.cac_number,
                    "moh_licence_number": organisation.moh_licence_number,
                    "medical_director_mdcn": organisation.medical_director_mdcn,
                    "contact_email": organisation.contact_email,
                    "contact_phone": organisation.contact_phone,
                    "share_records_across_branches": organisation.share_records_across_branches,
                },
                "branches": [
                    {
                        "id": str(branch.pk),
                        "name": branch.name,
                        "code": branch.code,
                        "state": branch.state,
                        "lga": branch.lga,
                        "address": branch.address,
                        "phone": branch.phone,
                        "is_main": branch.is_main,
                    }
                    for branch in Branch.objects.all()
                ],
            }
        )


class ApplyStepView(CuraviaAPIView):
    """Apply one step of the wizard."""

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["org.settings.change"]

    @extend_schema(
        request=None,
        responses={
            200: StepResultSerializer,
            400: OpenApiResponse(description="The step could not be applied"),
        },
    )
    def post(self, request: CuraviaRequest, step: str) -> Response:
        organisation = request.organisation
        if organisation is None:
            return Response(
                {"detail": "You are not signed in to a hospital.", "code": "no_organisation"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if step not in APPLY:
            return Response(
                {"detail": f"There is no setup step called {step!r}.", "code": "unknown_step"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        apply, shape = APPLY[step]
        payload: Any = request.data if hasattr(request.data, "get") else {}

        try:
            if shape == "object":
                result = apply(organisation, dict(payload))
            elif shape == "rows":
                result = apply(organisation, list(payload.get("rows") or []))
            else:  # branch_rows
                branch_id = payload.get("branch")
                if not branch_id:
                    return Response(
                        {"detail": "Which branch is this for?", "code": "branch_required"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                rows = payload.get("rows") or payload.get("codes") or []
                result = apply(organisation, str(branch_id), list(rows))
        except setup.SetupError as exc:
            return Response(
                {"detail": str(exc), "code": "setup_failed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit.record(
            "org.setup_step",
            actor=request.user,
            object_type="Organisation",
            object_id=organisation.pk,
            changes={"step": step, "created": result.created, "updated": result.updated},
            **as_audit_kwargs(),
        )

        organisation.refresh_from_db()
        return Response(
            {
                "created": result.created,
                "updated": result.updated,
                "detail": result.detail,
                "complete": setup_is_complete(organisation),
                "next_step": next_step(organisation),
            }
        )


class SkipStepView(CuraviaAPIView):
    """
    Mark an optional step as deliberately skipped.

    Recorded as skipped rather than done, so a hospital that said "no wards"
    can be told later that it never set them up, instead of the system
    pretending the work happened.
    """

    permission_classes = [IsAuthenticated, HasPermission]
    required_permissions = ["org.settings.change"]

    @extend_schema(request=None, responses={200: StepResultSerializer})
    def post(self, request: CuraviaRequest, step: str) -> Response:
        organisation = request.organisation
        if organisation is None:
            return Response(
                {"detail": "You are not signed in to a hospital.", "code": "no_organisation"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        required = {s.key for s in SETUP_STEPS if s.required}
        if step in required:
            return Response(
                {
                    "detail": "That step is needed before the hospital can be used.",
                    "code": "step_required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            mark_step(organisation, step, skipped=True)
        except ValueError as exc:
            return Response(
                {"detail": str(exc), "code": "unknown_step"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        audit.record(
            "org.setup_step_skipped",
            actor=request.user,
            object_type="Organisation",
            object_id=organisation.pk,
            changes={"step": step},
            **as_audit_kwargs(),
        )

        organisation.refresh_from_db()
        return Response(
            {
                "created": 0,
                "updated": 0,
                "detail": "Skipped.",
                "complete": setup_is_complete(organisation),
                "next_step": next_step(organisation),
            }
        )
