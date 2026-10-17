"""
WebSockets: tenant scope and the base consumer.

The live journey board, the pharmacy queue and the critical-result alert all
arrive over a socket, which means the tenant boundary has to hold here exactly
as it does over HTTP. Two rules:

1. **The scope comes from the session, never from the socket.** A client can
   put anything in a query string. What it cannot do is invent a membership.
2. **Group names always contain the organisation id.** A pharmacist in Lifeline
   Ikeja subscribes to ``org.<uuid>.branch.<uuid>.pharmacy``, so there is no
   group name another hospital could guess or collide with. Getting this wrong
   is how live-update features leak across tenants, and it would not show up in
   any HTTP test.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

logger = logging.getLogger("curavia.tenancy")

CLOSE_UNAUTHENTICATED = 4401
CLOSE_NO_MEMBERSHIP = 4403
CLOSE_DEACTIVATED = 4410


class TenantScopeWebsocketMiddleware:
    """
    Resolve the connecting user's hospital and branch, and put them in the
    connection scope for consumers to use.

    Runs after Channels' ``AuthMiddlewareStack``, so ``scope["user"]`` is
    populated. A user with no live membership is allowed to connect only so the
    consumer can close with a specific code — a silent drop is impossible to
    debug from the browser.
    """

    def __init__(self, inner: Callable[..., Awaitable[Any]]) -> None:
        self.inner = inner

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> Any:
        user = scope.get("user")
        scope["organisation_id"] = None
        scope["branch_id"] = None
        scope["permission_codes"] = frozenset()

        if user is not None and user.is_authenticated:
            resolved = await self._resolve(user)
            if resolved is not None:
                organisation_id, branch_id, codes = resolved
                scope["organisation_id"] = organisation_id
                scope["branch_id"] = branch_id
                scope["permission_codes"] = codes

        return await self.inner(scope, receive, send)

    @database_sync_to_async
    def _resolve(self, user):  # type: ignore[no-untyped-def]
        membership = (
            user.live_memberships().first()
            if not user.last_organisation_id
            else user.membership_for(user.last_organisation_id) or user.live_memberships().first()
        )
        if membership is None:
            return None
        codes = user.permission_codes(membership.organisation_id)
        branch_id = membership.default_branch_id
        if branch_id is None:
            first = membership.branches.first()
            branch_id = first.pk if first else None
        if user.last_branch_id and membership.branches.filter(pk=user.last_branch_id).exists():
            branch_id = user.last_branch_id
        return membership.organisation_id, branch_id, codes


class TenantConsumer(AsyncJsonWebsocketConsumer):
    """
    Base class for every Curavia consumer.

    Subclasses declare ``required_permission`` and ``group_suffix``; this class
    handles authentication, the permission check, group naming and the forced
    disconnect that staff deactivation triggers.
    """

    #: Permission code the user must hold to subscribe at all.
    required_permission: str | None = None
    #: Appended to the organisation/branch prefix to form the group name.
    group_suffix: str = ""
    #: True when the stream is per-branch rather than per-hospital.
    branch_scoped: bool = True

    groups: list[str]

    async def connect(self) -> None:
        user = self.scope.get("user")
        if not (user and user.is_authenticated):
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return
        if not user.is_active:
            await self.close(code=CLOSE_DEACTIVATED)
            return

        self.organisation_id: uuid.UUID | None = self.scope.get("organisation_id")
        self.branch_id: uuid.UUID | None = self.scope.get("branch_id")
        codes: frozenset[str] = self.scope.get("permission_codes", frozenset())

        if self.organisation_id is None:
            await self.close(code=CLOSE_NO_MEMBERSHIP)
            return
        if self.required_permission and self.required_permission not in codes:
            logger.warning("websocket refused: user %s lacks %s", user.pk, self.required_permission)
            await self.close(code=CLOSE_NO_MEMBERSHIP)
            return

        self._group_names = [self.group_name(), self.user_group_name()]
        for group in self._group_names:
            await self.channel_layer.group_add(group, self.channel_name)

        await self.accept()
        await self.send_json(
            {
                "type": "ready",
                "organisation": str(self.organisation_id),
                "branch": str(self.branch_id) if self.branch_id else None,
            }
        )

    async def disconnect(self, code: int) -> None:
        for group in getattr(self, "_group_names", []):
            await self.channel_layer.group_discard(group, self.channel_name)

    # -- group naming ----------------------------------------------------

    def group_name(self) -> str:
        """
        Always namespaced by organisation, and by branch when the stream is a
        branch's. This is the tenant boundary for live updates.
        """
        parts = [f"org.{self.organisation_id}"]
        if self.branch_scoped and self.branch_id:
            parts.append(f"branch.{self.branch_id}")
        if self.group_suffix:
            parts.append(self.group_suffix)
        return ".".join(parts)

    def user_group_name(self) -> str:
        """Per-user channel: notifications, and the forced-disconnect signal."""
        return f"user.{self.scope['user'].pk}"

    # -- handlers --------------------------------------------------------

    async def force_disconnect(self, event: dict[str, Any]) -> None:
        """
        Sent by ``accounts.signals`` when a staff member is deactivated.

        Without this, a deactivated nurse's open ward board would keep
        streaming patient updates until she closed the tab.
        """
        await self.close(code=CLOSE_DEACTIVATED)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Relay a server-side event to the browser, minus its routing key."""
        payload = {key: value for key, value in event.items() if key != "type"}
        await self.send_json(payload)


class NotificationConsumer(TenantConsumer):
    """
    The notification stream every signed-in staff member holds open.

    Milestone 2 ships only this one, because it is what proves the plumbing:
    authentication, tenant-scoped group naming, and immediate disconnect on
    deactivation. The journey board, pharmacy queue and critical-result
    consumers arrive with their modules.
    """

    group_suffix = "notifications"
    branch_scoped = False

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        # The client only ever pings; everything else is server-pushed.
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})
