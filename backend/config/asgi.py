"""
ASGI entry point — HTTP and WebSockets in one process.

Curavia's most visible feature is the live patient journey board, so WebSockets
are not an add-on here; Daphne serves both protocols and the deployment has no
separate socket server to keep in step.

The WebSocket stack mirrors the HTTP one deliberately: sessions, then
authentication, then tenant scope. A consumer must not be able to subscribe to
a hospital the connecting user does not work at, and the only way to guarantee
that is to resolve the scope from the session exactly as the HTTP middleware
does — never from anything the client sends.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# Django must be set up before anything imports a model.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from apps.core.ws import TenantScopeWebsocketMiddleware  # noqa: E402
from config.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(TenantScopeWebsocketMiddleware(URLRouter(websocket_urlpatterns)))
        ),
    }
)
