"""
WSGI entry point.

Kept for management tooling and for anything that cannot speak ASGI. The real
deployment runs ``config.asgi`` under Daphne, because WebSockets are not
optional in Curavia.
"""

from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_wsgi_application()
