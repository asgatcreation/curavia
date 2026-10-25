"""
Spreadsheet import: analyse, preview, commit.

**The file is uploaded with each of the three calls.** That looks wasteful
and is the right trade: the alternative is parking a parsed sheet on the
server between requests, which means somewhere to put it, a lifetime to
manage, a cleanup job, and a window where one hospital's file is sitting in a
cache keyed by something. Files here are capped at five megabytes; re-sending
one twice is cheaper than any of that.

It also makes commit honest. It re-reads and re-validates from the bytes
rather than trusting a token that says a preview once passed, so what gets
written is what the person actually approved — not what a stale server-side
state thinks they approved.

Everything is gated on the permission that owns the data being written:
``billing.price.manage`` for a price list, ``staff.invite`` for people. An
import is not a separate kind of power.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core import audit
from apps.core.api.base import CuraviaAPIView
from apps.core.api.types import CuraviaRequest
from apps.core.permissions import HasPermission
from apps.core.request_context import as_audit_kwargs
from apps.onboarding.importing import (
    IMPORTS,
    SpreadsheetError,
    build_preview,
    read_upload,
    suggest_mapping,
)

#: Which permission each import needs. Not one "import" permission: writing a
#: price list and inviting staff are different powers, and somebody who can
#: do one has no business doing the other because the file arrived as a
#: spreadsheet.
REQUIRED_PERMISSION = {
    "services": "billing.price.manage",
    "payers": "billing.price.manage",
    "staff": "staff.invite",
}

#: How many validated rows the preview sends back. The counts are always
#: exact; this caps the list, because a 5,000-row preview is a 2 MB response
#: nobody scrolls through.
PREVIEW_ROWS = 50
#: Errors are capped higher: the error report is the thing somebody actually
#: works from, and they need all of them to fix the file in one pass.
PREVIEW_ERRORS = 500


class ImportKindSerializer(serializers.Serializer):
    kind = serializers.CharField()
    title = serializers.CharField()
    blurb = serializers.CharField()
    columns = serializers.ListField(child=serializers.DictField())


class AnalyseResponseSerializer(serializers.Serializer):
    headers = serializers.ListField(child=serializers.CharField())
    row_count = serializers.IntegerField()
    truncated = serializers.BooleanField()
    suggested_mapping = serializers.DictField()
    sample = serializers.ListField(child=serializers.DictField())
    columns = serializers.ListField(child=serializers.DictField())


class PreviewResponseSerializer(serializers.Serializer):
    ok = serializers.IntegerField()
    failed = serializers.IntegerField()
    truncated = serializers.BooleanField()
    rows = serializers.ListField(child=serializers.DictField())
    # `errors` is a real field name in the response and also an attribute on
    # Serializer itself, so the stubs object to the assignment. These
    # serialisers are only ever used to document the schema - nothing calls
    # .is_valid() on them - so the shadowing is harmless and named here
    # rather than renaming a field the client reads.
    errors = serializers.ListField(child=serializers.DictField())  # type: ignore[assignment]


class CommitResponseSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    updated = serializers.IntegerField()
    skipped = serializers.IntegerField()
    errors = serializers.ListField(child=serializers.DictField())  # type: ignore[assignment]


class _ImportViewBase(CuraviaAPIView):
    """Shared plumbing: find the spec, check the permission, read the file."""

    permission_classes = [IsAuthenticated, HasPermission]
    parser_classes = [MultiPartParser, FormParser]

    def _spec_or_error(self, request: CuraviaRequest, kind: str):
        spec = IMPORTS.get(kind)
        if spec is None:
            return None, Response(
                {"detail": f"There is no import called {kind!r}.", "code": "unknown_import"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        needed = REQUIRED_PERMISSION.get(kind)
        if needed and needed not in request.tenant_permissions:
            return None, Response(
                {"detail": "Your role does not allow this.", "code": "permission_denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if request.organisation is None:
            return None, Response(
                {"detail": "You are not signed in to a hospital.", "code": "no_organisation"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return spec, None

    def _sheet_or_error(self, request: CuraviaRequest):
        upload = request.FILES.get("file")
        if upload is None:
            return None, Response(
                {"detail": "Choose a file to upload.", "code": "no_file"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            sheet = read_upload(name=upload.name or "upload.csv", data=upload.read())
        except SpreadsheetError as exc:
            return None, Response(
                {"detail": str(exc), "code": "bad_file"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return sheet, None

    @staticmethod
    def _mapping(request: CuraviaRequest) -> dict[str, str]:
        """
        The column mapping, sent as JSON inside a multipart form.

        It travels beside the file rather than in a JSON body because the
        file has to be multipart, and splitting one submission across two
        requests to keep the mapping JSON would be worse.
        """
        import json

        # request.data is a QueryDict for multipart; DRF types it as a union
        # with list for the parser that returns one.
        data = request.data
        raw = data.get("mapping") if hasattr(data, "get") else None
        if not raw:
            return {}
        if isinstance(raw, dict):
            return {str(key): str(value) for key, value in raw.items()}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(key): str(value) for key, value in parsed.items() if value}


class ImportCatalogueView(CuraviaAPIView):
    """What can be imported, and what columns each one understands."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: ImportKindSerializer(many=True)})
    def get(self, request: CuraviaRequest) -> Response:
        held = request.tenant_permissions
        return Response(
            [
                {
                    "kind": spec.kind,
                    "title": spec.title,
                    "blurb": spec.blurb,
                    "columns": [
                        {
                            "key": column.key,
                            "label": column.label,
                            "required": column.required,
                            "help_text": column.help_text,
                            "example": column.example,
                        }
                        for column in spec.columns
                    ],
                }
                for spec in IMPORTS.values()
                if REQUIRED_PERMISSION.get(spec.kind, "") in held
            ]
        )


class AnalyseImportView(_ImportViewBase):
    """Step one: what is in this file, and what do we think it means."""

    required_permissions: list[str] = []

    @extend_schema(
        request=None,
        responses={
            200: AnalyseResponseSerializer,
            400: OpenApiResponse(description="The file could not be read"),
        },
    )
    def post(self, request: CuraviaRequest, kind: str) -> Response:
        spec, error = self._spec_or_error(request, kind)
        if error is not None:
            return error
        sheet, error = self._sheet_or_error(request)
        if error is not None or sheet is None or spec is None:
            return error  # type: ignore[return-value]

        return Response(
            {
                "headers": sheet.headers,
                "row_count": len(sheet.rows),
                "truncated": sheet.truncated,
                "suggested_mapping": suggest_mapping(spec, sheet.headers),
                # Five rows is enough to see whether the mapping is right and
                # short enough to read without scrolling.
                "sample": sheet.as_dicts()[:5],
                "columns": [
                    {
                        "key": column.key,
                        "label": column.label,
                        "required": column.required,
                        "help_text": column.help_text,
                        "example": column.example,
                    }
                    for column in spec.columns
                ],
            }
        )


class PreviewImportView(_ImportViewBase):
    """Step two: every row validated. Writes nothing."""

    required_permissions: list[str] = []

    @extend_schema(
        request=None,
        responses={
            200: PreviewResponseSerializer,
            400: OpenApiResponse(description="The file could not be read"),
        },
    )
    def post(self, request: CuraviaRequest, kind: str) -> Response:
        spec, error = self._spec_or_error(request, kind)
        if error is not None:
            return error
        sheet, error = self._sheet_or_error(request)
        if error is not None or sheet is None or spec is None:
            return error  # type: ignore[return-value]

        result = build_preview(
            spec=spec,
            organisation=request.organisation,  # type: ignore[arg-type]
            sheet=sheet,
            mapping=self._mapping(request),
        )

        return Response(
            {
                "ok": result.ok,
                "failed": result.failed,
                "truncated": result.truncated,
                "rows": [
                    {"row_number": row.row_number, "summary": row.summary}
                    for row in result.ready[:PREVIEW_ROWS]
                ],
                "errors": [
                    {
                        "row_number": item.row_number,
                        "column": item.column,
                        "message": item.message,
                    }
                    for item in result.errors[:PREVIEW_ERRORS]
                ],
            }
        )


class CommitImportView(_ImportViewBase):
    """Step three: validate again, then write the rows that pass."""

    required_permissions: list[str] = []

    @extend_schema(
        request=None,
        responses={
            200: CommitResponseSerializer,
            400: OpenApiResponse(description="Nothing could be imported"),
        },
    )
    def post(self, request: CuraviaRequest, kind: str) -> Response:
        spec, error = self._spec_or_error(request, kind)
        if error is not None:
            return error
        sheet, error = self._sheet_or_error(request)
        if error is not None or sheet is None or spec is None:
            return error  # type: ignore[return-value]

        organisation = request.organisation
        result = build_preview(
            spec=spec,
            organisation=organisation,  # type: ignore[arg-type]
            sheet=sheet,
            mapping=self._mapping(request),
        )

        if not result.ready:
            return Response(
                {
                    "detail": "Nothing in that file could be imported.",
                    "code": "nothing_to_import",
                    "errors": [
                        {
                            "row_number": item.row_number,
                            "column": item.column,
                            "message": item.message,
                        }
                        for item in result.errors[:PREVIEW_ERRORS]
                    ],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The staff importer needs to know who is inviting, which the
        # validator cannot: it has no request. Injected here rather than
        # threaded through every validator's signature.
        if kind == "staff":
            for row in result.ready:
                row.values["invited_by"] = request.user
                row.values["invited_by_name"] = request.user.full_name

        created, updated = spec.write(organisation, result.ready)  # type: ignore[arg-type]

        audit.record(
            "org.import",
            actor=request.user,
            object_type="Import",
            changes={
                "kind": kind,
                "created": created,
                "updated": updated,
                "skipped": result.failed,
            },
            **as_audit_kwargs(),
        )

        return Response(
            {
                "created": created,
                "updated": updated,
                # The rows that did not import are reported, not hidden. A
                # hospital whose 900-line list imported 897 rows needs the
                # three.
                "skipped": result.failed,
                "errors": [
                    {
                        "row_number": item.row_number,
                        "column": item.column,
                        "message": item.message,
                    }
                    for item in result.errors[:PREVIEW_ERRORS]
                ],
            }
        )


__all__ = [
    "AnalyseImportView",
    "CommitImportView",
    "ImportCatalogueView",
    "PreviewImportView",
]
