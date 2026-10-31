"""
Authenticating a WebSocket from a token-based app.

The problem, found by trying to open the journey board's socket from the real
staff app: Channels' ``AuthMiddlewareStack`` resolves the user from a **Django
session cookie**, and Curavia's sign-in issues JWTs and creates no session. So
every socket the SPA opened would close with 4401, and no HTTP test would ever
have noticed — the same shape of bug as the one recorded in
``apps/core/api/base.py``, where session auth hid a JWT failure, only
inverted.

Three ways to fix it, and why this is the one:

**Put the access token in the query string.** Rejected. URLs end up in access
logs, proxy logs, browser history and ``Referer`` headers. The access token is
a bearer credential for a hospital's patient data, and a fifteen-minute window
is plenty.

**Create a Django session at login as well.** Rejected. It means a second
credential with its own lifetime, its own CSRF story and its own revocation
path, kept in step with the first one forever.

**A short-lived, single-use ticket.** Chosen. The client asks for one over the
authenticated HTTP API, gets an opaque string good for thirty seconds and one
connection, and puts that in the socket URL. If it leaks into a log it is
already spent and already expired. A reconnect fetches a new one, which has
the useful side effect of re-checking on every reconnect that the member of
staff still works here.

The nonce lives in the cache. If the cache has lost it, the ticket is refused
— failing closed, because an unreplayable ticket that occasionally makes
somebody reconnect is better than a replayable one that never does.
"""

from __future__ import annotations

import logging
import secrets

from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

logger = logging.getLogger("curavia.ws")

#: Salt, so a ticket cannot be swapped for a signed value from anywhere else
#: in the system that happens to use the same key.
TICKET_SALT = "curavia.websocket.ticket"

#: Thirty seconds is comfortably longer than a page needs to open a socket
#: after the fetch that produced the ticket, and short enough that a leaked
#: URL in a log is of no use to anybody.
TICKET_MAX_AGE = 30

#: Cache prefix for the single-use nonce.
NONCE_PREFIX = "ws-ticket:"


def issue_ticket(user) -> tuple[str, int]:
    """
    Mint a ticket for this user. Returns ``(ticket, seconds_valid)``.

    The nonce is stored in the cache for slightly longer than the signature
    is valid, so a ticket always fails on its signature first and the error
    the client sees is the honest one.
    """
    nonce = secrets.token_urlsafe(16)
    cache.set(f"{NONCE_PREFIX}{nonce}", str(user.pk), timeout=TICKET_MAX_AGE + 10)
    signer = TimestampSigner(salt=TICKET_SALT)
    return signer.sign(f"{user.pk}:{nonce}"), TICKET_MAX_AGE


def redeem_ticket(ticket: str) -> str | None:
    """
    Check a ticket and spend it. Returns the user id, or None.

    ``cache.delete`` after reading is the single-use part. It is not an
    atomic compare-and-delete, so two sockets opened in the same millisecond
    with the same ticket could both pass — which is a far smaller problem
    than it sounds, since both would be the same user with the same
    permissions, and the window is one round trip wide.
    """
    if not ticket:
        return None

    signer = TimestampSigner(salt=TICKET_SALT)
    try:
        value = signer.unsign(ticket, max_age=TICKET_MAX_AGE)
    except SignatureExpired:
        logger.info("websocket ticket expired")
        return None
    except BadSignature:
        logger.warning("websocket ticket failed its signature")
        return None

    user_id, _, nonce = value.partition(":")
    if not user_id or not nonce:  # pragma: no cover - our own format
        return None

    key = f"{NONCE_PREFIX}{nonce}"
    stored = cache.get(key)
    if stored is None:
        # Already used, or the cache lost it. Either way the client fetches
        # another one and reconnects; see the module docstring.
        logger.info("websocket ticket already spent")
        return None
    cache.delete(key)

    if stored != user_id:  # pragma: no cover - defensive
        logger.warning("websocket ticket nonce belonged to a different user")
        return None
    return user_id


class TicketAuthMiddleware:
    """
    Put the ticket's user into the connection scope.

    Sits between ``AuthMiddlewareStack`` and the tenant-scope middleware, so
    a session still works where there is one and a ticket is used where
    there is not. It never *removes* a user: a request that arrives with a
    valid session and a bad ticket keeps the session.
    """

    def __init__(self, inner) -> None:
        self.inner = inner

    async def __call__(self, scope, receive, send):
        existing = scope.get("user")
        if existing is not None and getattr(existing, "is_authenticated", False):
            return await self.inner(scope, receive, send)

        ticket = _ticket_from(scope)
        if ticket:
            user = await _user_for(ticket)
            if user is not None:
                scope["user"] = user
        return await self.inner(scope, receive, send)


def _ticket_from(scope) -> str:
    from urllib.parse import parse_qs

    raw = scope.get("query_string", b"")
    if not raw:
        return ""
    values = parse_qs(raw.decode("utf-8", "ignore")).get("ticket", [])
    return values[0] if values else ""


async def _user_for(ticket: str):
    from channels.db import database_sync_to_async

    @database_sync_to_async
    def load():
        user_id = redeem_ticket(ticket)
        if user_id is None:
            return None
        from apps.accounts.models import User

        return User.objects.filter(pk=user_id, is_active=True).first()

    return await load()


__all__ = [
    "TICKET_MAX_AGE",
    "TicketAuthMiddleware",
    "issue_ticket",
    "redeem_ticket",
]
