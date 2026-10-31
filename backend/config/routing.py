"""WebSocket routes."""

from __future__ import annotations

from django.urls import path

from apps.core.ws import NotificationConsumer
from apps.journey.consumers import DisplayConsumer, JourneyConsumer

websocket_urlpatterns = [
    path("ws/notifications/", NotificationConsumer.as_asgi(), name="ws-notifications"),
    path("ws/journey/", JourneyConsumer.as_asgi(), name="ws-journey"),
    # The television. Its token is in the path rather than a query string so
    # it is not stripped by anything that rewrites query parameters, and
    # because the URL is typed in once and then left alone for years.
    path("ws/display/<str:token>/", DisplayConsumer.as_asgi(), name="ws-display"),
]
