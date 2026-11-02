"""
One tiny JSON-over-HTTP helper, on the standard library.

``requests`` is not a dependency and is not being added for this. Every
outbound call Curavia makes to a provider is a single JSON POST with a header
and a timeout, which ``urllib`` does in twenty lines — and a dependency that
exists to save twenty lines is a dependency that still has to be patched when
it has a CVE.

The important part is not the transport, it is the contract: **this never
raises.** A provider being down, slow, or returning nonsense must come back as
a value the caller can record and retry, because the caller is usually a
background job sending somebody an appointment reminder and a traceback there
is a reminder nobody ever gets.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("curavia.integrations")

#: Short on purpose. A reminder is queued work with a retry behind it, so
#: waiting thirty seconds on a hanging provider only delays the next message.
DEFAULT_TIMEOUT = 10


@dataclass(frozen=True, slots=True)
class HttpResult:
    ok: bool
    status: int
    data: dict[str, Any]
    error: str = ""


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> HttpResult:
    """POST JSON and read JSON back. Returns a result rather than raising."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - the URL is ours, from settings
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8", "replace")
            return HttpResult(ok=True, status=response.status, data=_parse(raw))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        parsed = _parse(raw)
        # The provider's own message, where there is one: "insufficient
        # balance" is something a hospital can act on, and "HTTP 400" is not.
        message = str(parsed.get("message") or parsed.get("error") or raw[:200] or exc.reason)
        return HttpResult(ok=False, status=exc.code, data=parsed, error=message)
    except (urllib.error.URLError, TimeoutError) as exc:
        return HttpResult(ok=False, status=0, data={}, error=f"could not reach {url}: {exc}")
    except Exception as exc:  # a provider must never crash the caller
        logger.exception("unexpected error calling %s", url)
        return HttpResult(ok=False, status=0, data={}, error=str(exc))


def _parse(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"raw": raw[:500]}
    return value if isinstance(value, dict) else {"raw": value}


__all__ = ["DEFAULT_TIMEOUT", "HttpResult", "post_json"]
