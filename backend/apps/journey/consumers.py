"""
The two sockets the journey board needs.

``JourneyConsumer`` is a member of staff watching their branch: authenticated,
tenant-scoped, and carrying patient names because the person reading it is
treating those patients.

``DisplayConsumer`` is a television. It is not a person, it has no membership,
and the only thing identifying it is a token in its URL. It therefore gets a
separate consumer that does not inherit the staff base class at all — not a
flag on the same one. A shared consumer with ``if screen: strip_names()`` is
one refactor away from a waiting room full of people reading somebody's
surname off a wall, and the way to make that impossible is for the code that
could do it not to exist.

Both send a full snapshot on connect. The live updates are deltas, so a
client that missed a message while reconnecting would otherwise be wrong
until the next change — which, in a quiet pharmacy at four in the afternoon,
could be twenty minutes.
"""

from __future__ import annotations

import logging
from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone

from apps.core.tenancy import tenant_scope
from apps.core.ws import CLOSE_NO_MEMBERSHIP, TenantConsumer
from apps.journey import service
from apps.journey.models import DisplayScreen

logger = logging.getLogger("curavia.journey")

CLOSE_UNKNOWN_SCREEN = 4404


class JourneyConsumer(TenantConsumer):
    """
    The live board for one branch.

    Inherits the tenant rules from ``apps/core/ws.py``: the scope comes from
    the connection and never from the client, and the group name carries the
    organisation id so no two hospitals can share a stream.
    """

    required_permission = "journey.view"
    group_suffix = "journey"
    branch_scoped = True

    async def connect(self) -> None:
        await super().connect()
        # `connect` closes rather than raising when it refuses, so there is
        # no group to send to and nothing to snapshot.
        if not getattr(self, "_group_names", None):
            return
        await self.send_snapshot()

    async def send_snapshot(self) -> None:
        rows = await self._rows()
        await self.send_json({"type": "snapshot", "rows": rows, "at": timezone.now().isoformat()})

    @database_sync_to_async
    def _rows(self) -> list[dict[str, Any]]:
        # The board reads patient rows, so it runs inside the tenant scope
        # like any other read — the managers refuse otherwise, which is the
        # protection working rather than an inconvenience.
        if self.organisation_id is None:  # pragma: no cover - connect() refuses first
            return []
        with tenant_scope(self.organisation_id, self.branch_id, all_branches=False):
            return service.board_rows(self.branch_id)

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        action = content.get("type")
        if action == "ping":
            await self.send_json({"type": "pong"})
        elif action == "refresh":
            # The client asks for this after a reconnect, and whenever the
            # tab has been in the background long enough to distrust what it
            # is showing.
            await self.send_snapshot()


class DisplayConsumer(AsyncJsonWebsocketConsumer):
    """
    A waiting-room television.

    Deliberately **not** a ``TenantConsumer``: there is no user, no
    membership and no permission code. What there is instead is a token in
    the URL that identifies one screen at one branch, and a payload built by
    ``service.display_rows`` — the only function allowed to build it, and one
    that puts no names in it.

    The screen is read-only. Anything sent up this socket other than a ping
    is ignored rather than answered, because the only thing on the other end
    is a browser nobody is sitting at.
    """

    async def connect(self) -> None:
        token = self.scope["url_route"]["kwargs"].get("token", "")
        screen = await self._screen(token)
        if screen is None:
            await self.close(code=CLOSE_UNKNOWN_SCREEN)
            return

        self.screen_pk = screen["pk"]
        self.organisation_id = screen["organisation_id"]
        self.branch_id = screen["branch_id"]
        self.group = screen["group"]

        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self.send_payload()

    async def disconnect(self, code: int) -> None:
        group = getattr(self, "group", None)
        if group:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def broadcast(self, event: dict[str, Any]) -> None:
        """
        A nudge arrived. Re-read this screen's own numbers and send them.

        The nudge carries no data — see ``service.refresh_displays`` — so
        each screen builds only what it shows.
        """
        await self.send_payload()

    async def send_payload(self) -> None:
        payload = await self._payload()
        if payload is not None:
            await self.send_json({"type": "display", **payload})

    @database_sync_to_async
    def _screen(self, token: str) -> dict[str, Any] | None:
        """
        Find the screen by its token.

        Unscoped on purpose and by necessity: nobody is signed in, so the
        token is the only thing that says which hospital this is. It is read
        through the unscoped manager with a reason, which is what keeps it
        visible in the cross-tenant allowlist, and the scope is then set from
        the screen itself for everything that follows.
        """
        from apps.core.tenancy import unscoped

        if not token:
            return None
        with unscoped(reason="display screen: a television has no session, only its token"):
            screen = (
                DisplayScreen.unscoped.filter(token=token, is_active=True)
                .select_related("organisation", "branch")
                .first()
            )
            if screen is None:
                return None
            DisplayScreen.unscoped.filter(pk=screen.pk).update(last_seen_at=timezone.now())
            return {
                "pk": screen.pk,
                "organisation_id": screen.organisation_id,
                "branch_id": screen.branch_id,
                "group": service.display_group(screen),
            }

    @database_sync_to_async
    def _payload(self) -> dict[str, Any] | None:
        with tenant_scope(self.organisation_id, self.branch_id):
            screen = DisplayScreen.objects.filter(pk=self.screen_pk).first()
            if screen is None:  # pragma: no cover - deleted mid-connection
                return None
            return service.display_rows(screen)


__all__ = ["CLOSE_NO_MEMBERSHIP", "CLOSE_UNKNOWN_SCREEN", "DisplayConsumer", "JourneyConsumer"]
