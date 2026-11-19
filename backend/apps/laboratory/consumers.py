"""
The live laboratory worklist.

One socket, watched by three different people: a nurse who wants to know
when blood needs drawing, a scientist who wants to know when a tube is on
its way, and a doctor who wants to know whether their test has started.
They all read the same stream and filter it on the screen, because the
underlying event is the same — something on this branch's worklist changed.

**Nudges, not deltas.** The server sends ``{"type": "changed", "reason":
...}`` and the client re-reads ``/lab/worklist/``. A delta would be smaller,
but the worklist at a private hospital is tens of rows, each screen shows a
different slice of it, and a client that missed one message while its phone
was switching from wi-fi to mobile data would then be quietly wrong. Asking
the server again is always right, and the reason is carried so the screen
can say what happened without having to work it out.

Subscribing needs ``lab.order.view`` — the same permission as reading the
worklist, since a socket that announced changes to people who cannot read
them would be a way of counting a hospital's work from outside it.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.core.ws import TenantConsumer
from apps.laboratory import worklist

logger = logging.getLogger("curavia.laboratory")


class WorklistConsumer(TenantConsumer):
    """
    The live worklist for one branch.

    Branch-scoped, not organisation-scoped: a tube is a physical object in
    one building, and a scientist at Lekki has no use for being told that
    somebody at Ikeja drew blood.

    The group name is built by ``worklist.worklist_group`` so the writer and
    the reader cannot disagree about it — a mismatch there is a board that
    silently never updates, which is the hardest kind of bug to notice.
    """

    required_permission = "lab.order.view"
    group_suffix = "lab"
    branch_scoped = True

    def group_name(self) -> str:
        return worklist.worklist_group(self.organisation_id, self.branch_id)

    async def connect(self) -> None:
        await super().connect()
        # `connect` closes rather than raising when it refuses, in which case
        # there is no group and nothing to announce.
        if not getattr(self, "_group_names", None):
            return
        await self.send_json({"type": "changed", "reason": "connected"})

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        if content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Pass the nudge on. The client then re-reads the worklist."""
        await self.send_json({"type": "changed", "reason": event.get("reason", "")})


__all__ = ["WorklistConsumer"]
