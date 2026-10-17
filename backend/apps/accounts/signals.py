"""
Deactivating a staff member takes effect immediately.

"Their access is removed" is easy to claim and easy to get wrong. A session
cookie is valid until it expires, and an open WebSocket keeps streaming the
ward's updates long after HR has switched someone off. So when a membership is
deactivated we do three things at once:

1. delete the user's Django sessions, so the next HTTP request is anonymous;
2. mark their ``UserSession`` rows revoked, so the device list tells the truth;
3. send a ``force_disconnect`` message to the user's channel group, so any open
   WebSocket closes rather than quietly continuing.

Signals are the right tool here specifically because this must happen however
the row was changed — the API, the admin, a management command or a shell.
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib.sessions.models import Session
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.core import audit

from .models import StaffMembership, User, UserSession

logger = logging.getLogger("curavia.audit")


def user_channel_group(user_id) -> str:
    """The per-user channel group every authenticated socket joins."""
    return f"user.{user_id}"


def revoke_all_sessions(user: User, *, reason: str) -> int:
    """
    End every session this user holds, everywhere, now.

    Returns how many were ended, which the API reports back so the person
    clicking "sign out everywhere" sees it worked.
    """
    keys = list(
        UserSession.objects.filter(user=user, revoked_at__isnull=True).values_list(
            "session_key", flat=True
        )
    )
    if keys:
        Session.objects.filter(session_key__in=keys).delete()
        UserSession.objects.filter(session_key__in=keys).update(revoked_at=timezone.now())

    _disconnect_sockets(user)

    audit.record(
        "user.sessions_revoked",
        actor=user,
        object_type="User",
        object_id=user.pk,
        changes={"sessions_ended": len(keys)},
        reason=reason,
    )
    logger.info("revoked %s session(s) for user %s: %s", len(keys), user.pk, reason)
    return len(keys)


def _disconnect_sockets(user: User) -> None:
    """
    Ask any open WebSocket for this user to close.

    Best effort on purpose: if Redis is unreachable the HTTP session is already
    gone, which is the part that matters, and a dropped socket cannot do
    anything without it.
    """
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(user_channel_group(user.pk), {"type": "force.disconnect"})
    except Exception:  # pragma: no cover - transport failure must not block
        logger.warning("could not reach the channel layer to disconnect user %s", user.pk)


@receiver(post_save, sender=StaffMembership, dispatch_uid="curavia_membership_deactivated")
def _on_membership_saved(sender, instance: StaffMembership, created: bool, **kwargs) -> None:
    if created or instance.is_active:
        return
    # Only cut sessions if this was their last live membership; a consultant
    # who leaves one hospital still works at the other two.
    if instance.user.live_memberships().exists():
        _disconnect_sockets(instance.user)
        return
    revoke_all_sessions(instance.user, reason="staff membership deactivated")


@receiver(post_save, sender=User, dispatch_uid="curavia_user_deactivated")
def _on_user_saved(sender, instance: User, created: bool, **kwargs) -> None:
    if created or instance.is_active:
        return
    revoke_all_sessions(instance, reason="user account deactivated")
