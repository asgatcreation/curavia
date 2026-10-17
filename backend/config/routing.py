"""WebSocket routes."""

from __future__ import annotations

from django.urls import path

from apps.core.ws import NotificationConsumer

websocket_urlpatterns = [
    path("ws/notifications/", NotificationConsumer.as_asgi(), name="ws-notifications"),
]
