"""
One error shape for the whole API, and one rule about what an error may say.

The front end should never have to guess where the message is, so every error
looks the same::

    {"detail": "...", "code": "permission_denied", "fields": {...}}

The rule about content: an error must not leak the existence of data the caller
may not see. "Patient 4b2… belongs to another hospital" confirms that the
patient exists. A tenant-scope miss is therefore a plain 404, and a
``TenantScopeMissing`` — which is our own bug, not the caller's — becomes a 500
and goes to Sentry, because returning anything else would hide a real defect.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from apps.core.tenancy import TenantScopeMissing

logger = logging.getLogger("curavia.tenancy")


def curavia_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    if isinstance(exc, TenantScopeMissing):
        # Ours, not theirs. Let it 500 so it is loud, and log the view.
        logger.error("tenant scope missing in %s: %s", context.get("view").__class__.__name__, exc)
        raise exc

    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(detail=getattr(exc, "message_dict", exc.messages))
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied(str(exc) or None)
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    return Response(
        _normalise(exc, response), status=response.status_code, headers=dict(response.headers)
    )


def _normalise(exc: Exception, response: Response) -> dict[str, Any]:
    code = getattr(exc, "default_code", "error")
    data = response.data

    if isinstance(data, dict) and "detail" in data and len(data) == 1:
        return {"detail": str(data["detail"]), "code": code}

    if isinstance(data, dict):
        # A serializer's field errors.
        return {
            "detail": "Some of the information you entered needs fixing.",
            "code": code,
            "fields": {
                field: [str(message) for message in _as_list(messages)]
                for field, messages in data.items()
            },
        }

    if isinstance(data, list):
        return {"detail": " ".join(str(item) for item in data), "code": code}

    return {"detail": str(data), "code": code}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


class ReadOnlyOrganisation(exceptions.APIException):
    """A hospital whose subscription lapsed can be read and exported, not changed."""

    status_code = status.HTTP_403_FORBIDDEN
    default_code = "organisation_read_only"
    default_detail = (
        "This hospital's subscription is not active, so the system is read-only. "
        "Records can still be read and exported."
    )


class ReasonRequired(exceptions.APIException):
    """Break-the-glass, waivers and overridden warnings all demand a reason."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "reason_required"
    default_detail = "A reason is required for this action, and it is recorded in the audit log."
