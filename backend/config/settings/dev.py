"""Local development settings."""

from .base import *
from .base import CURAVIA, REST_FRAMEWORK, env_bool, env_list

DEBUG = True
# 0.0.0.0 so a phone or tablet on the same network can reach the dev server,
# which is how the nurse-on-a-tablet layouts actually get checked.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]  # noqa: S104

# The two Vite dev servers.
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:5174"
)
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Browsable API is handy while building.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

CURAVIA = {**CURAVIA, "ENABLE_RLS": env_bool("CURAVIA_ENABLE_RLS", False)}
