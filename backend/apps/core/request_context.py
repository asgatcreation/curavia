"""
Who is calling, and from where.

The audit log needs the caller's IP, user agent and session key. Threading a
``request`` object down through every service function would couple the domain
layer to HTTP and would not work from a Celery task or a WebSocket consumer
anyway. A contextvar carries it instead, set once by middleware.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.core.api.types import AuditContext


@dataclass(frozen=True, slots=True)
class RequestInfo:
    ip: str | None = None
    user_agent: str = ""
    session_key: str = ""


_EMPTY = RequestInfo()

_context: ContextVar[RequestInfo] = ContextVar("curavia_request_info", default=_EMPTY)


def set(info: RequestInfo) -> Token[RequestInfo]:  # noqa: A001 - mirrors ContextVar.set
    return _context.set(info)


def reset(token: Token[RequestInfo]) -> None:
    _context.reset(token)


def get() -> RequestInfo:
    return _context.get()


def as_audit_kwargs() -> AuditContext:
    """
    Spread straight into ``apps.core.audit.record``.

    Typed as ``AuditContext`` so ``**as_audit_kwargs()`` is checked against
    ``record``'s signature rather than matching any keyword argument.
    """
    info = _context.get()
    return {"ip": info.ip, "user_agent": info.user_agent, "session_key": info.session_key}
