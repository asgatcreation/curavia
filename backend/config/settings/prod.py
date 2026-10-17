"""
Production settings.

Target today: one Render free web service running Daphne and a Celery worker
under honcho, with Neon Postgres, Upstash Redis and Cloudflare R2.
See docs/00-decisions.md §3.
"""

from __future__ import annotations

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration

from .base import *
from .base import CURAVIA, env, env_bool, env_list

DEBUG = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise RuntimeError("DJANGO_ALLOWED_HOSTS must be set in production.")

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

# --------------------------------------------------------------------------
# Transport security
# --------------------------------------------------------------------------

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False  # the SPA reads it to set the header

# --------------------------------------------------------------------------
# Private file storage — medical files, signed URLs only
# --------------------------------------------------------------------------

if env_bool("USE_OBJECT_STORAGE", False):  # switched on at milestone 7
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            # django-stubs types each STORAGES value as dict[str, str], so the
            # nested OPTIONS mapping - which Django itself expects - is flagged.
            "OPTIONS": {  # type: ignore[dict-item]
                "bucket_name": env("S3_BUCKET_NAME"),
                "endpoint_url": env("S3_ENDPOINT_URL"),
                "access_key": env("S3_ACCESS_KEY_ID"),
                "secret_key": env("S3_SECRET_ACCESS_KEY"),
                "region_name": env("S3_REGION", "auto"),
                # These three lines are the point: nothing is ever public.
                "default_acl": "private",
                "querystring_auth": True,
                "querystring_expire": CURAVIA["SIGNED_URL_TTL"],
                "file_overwrite": False,
                "signature_version": "s3v4",
            },
        },
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

# --------------------------------------------------------------------------
# Monitoring
# --------------------------------------------------------------------------

if sentry_dsn := env("SENTRY_DSN", ""):
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        environment=env("SENTRY_ENVIRONMENT", "production"),
        release=env("GIT_COMMIT", "unknown"),
        traces_sample_rate=float(env("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        # Patient data must never leave the hospital's database.
        send_default_pii=False,
        max_request_body_size="never",
    )

CURAVIA = {**CURAVIA, "ENABLE_RLS": env_bool("CURAVIA_ENABLE_RLS", False)}
