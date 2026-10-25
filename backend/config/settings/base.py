"""
Curavia — base settings shared by every environment.

Environment-specific settings live in dev.py, test.py and prod.py.
Nothing secret belongs in this file; secrets come from the environment.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
REPO_ROOT = BASE_DIR.parent

load_dotenv(REPO_ROOT / ".env")


def env(key: str, default: str | None = None) -> str:
    value = os.environ.get(key, default)
    if value is None:
        raise RuntimeError(
            f"Required environment variable {key} is not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-development-key-do-not-use-in-production")
DEBUG = False
ALLOWED_HOSTS: list[str] = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "corsheaders",
    "django_filters",
    "rules.apps.AutodiscoverRulesConfig",
    "allauth",
    "allauth.account",
    "allauth.mfa",
    "axes",
    "channels",
    # Curavia
    "apps.core",
    "apps.accounts",
    "apps.catalogue",
    "apps.onboarding",
    "apps.platformadmin",
    "apps.marketing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Curavia: must run after authentication so request.user is available.
    "apps.core.middleware.TenantScopeMiddleware",
    "apps.core.middleware.RequestContextMiddleware",
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "curavia"),
        "USER": env("POSTGRES_USER", "curavia"),
        "PASSWORD": env("POSTGRES_PASSWORD", "curavia"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(env("POSTGRES_CONN_MAX_AGE", "60")),
        "OPTIONS": {},
    }
}
if os.environ.get("DATABASE_URL"):
    # Neon and most hosts provide a single connection URL.
    import dj_database_url  # type: ignore[import-not-found]

    DATABASES["default"] = dj_database_url.parse(  # type: ignore[assignment]
        os.environ["DATABASE_URL"], conn_max_age=60, ssl_require=True
    )

# Every HTTP request runs in one transaction. The tenant middleware relies on
# this: it issues SET LOCAL app.current_org, which is transaction-scoped.
ATOMIC_REQUESTS = True
DATABASES["default"]["ATOMIC_REQUESTS"] = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    # Object-level rules are consulted first, then the normal backends.
    "rules.permissions.ObjectPermissionBackend",
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# allauth
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_RATE_LIMITS = {"login_failed": "5/5m"}
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "Curavia"

# Staff sessions expire; patients get a longer window set per-view.
SESSION_COOKIE_AGE = int(env("SESSION_COOKIE_AGE", "3600"))
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# django-axes: lockout with backoff
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.25  # 15 minutes
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]
AXES_RESET_ON_SUCCESS = True

# --------------------------------------------------------------------------
# DRF / OpenAPI
# --------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "apps.core.api.pagination.CuraviaPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"auth": "20/min", "search": "60/min", "default": "1000/hour"},
    "EXCEPTION_HANDLER": "apps.core.api.exceptions.curavia_exception_handler",
}

# --------------------------------------------------------------------------
# JWT
#
# Set explicitly rather than left on simplejwt's defaults, because the
# defaults are tuned for a consumer app and this is a clinical one.
#
# A twelve-hour refresh token is a shift. A nurse who signs in at the start of
# a shift should not be asked again before the end of it, and should not still
# be signed in the next morning on a machine in a corridor.
#
# Rotation with blacklisting means a refresh token is single-use: if one is
# stolen and used, the real user's next refresh fails and they are signed out,
# which is a detectable event rather than a silent parallel session.
# --------------------------------------------------------------------------

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=12),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Curavia API",
    "DESCRIPTION": (
        "Hospital and clinic operating system. Every endpoint is scoped to one "
        "hospital (Organisation) and governed by the permission matrix in "
        "docs/permissions-matrix.yml."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/v1",
}

# --------------------------------------------------------------------------
# Channels / Celery / Redis
# --------------------------------------------------------------------------

REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "Africa/Lagos"
CELERY_TASK_TIME_LIMIT = 600
CELERY_TASK_SOFT_TIME_LIMIT = 540
CELERY_WORKER_CONCURRENCY = int(env("CELERY_WORKER_CONCURRENCY", "1"))

# All background work goes through apps.core.tasks.enqueue(), so switching to
# Django's built-in database-backed tasks is a one-file change. See
# docs/00-decisions.md §3.
BACKGROUND_TASK_BACKEND = env("BACKGROUND_TASK_BACKEND", "celery")

# --------------------------------------------------------------------------
# Internationalisation — English first, i18n-ready from day one
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-ng"
LANGUAGES = [
    ("en", "English"),
    # Phase D: ("yo", "Yoruba"), ("ha", "Hausa"), ("ig", "Igbo"), ("pcm", "Nigerian Pidgin")
]
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static and media
# --------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

# Medical files NEVER go to a public bucket. Private S3/R2 with short-lived
# signed URLs only; see apps.core.storage and docs/02-tenancy-and-permissions.md.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# --------------------------------------------------------------------------
# Curavia-specific
# --------------------------------------------------------------------------

CURAVIA = {
    # Postgres row-level security as a second net under the ORM guard.
    # Requires a dedicated non-owner database role; off until that role exists.
    # See docs/adr/0002-row-level-security.md.
    "ENABLE_RLS": env_bool("CURAVIA_ENABLE_RLS", False),
    # Header a client uses to pick which of its branches it is working in.
    "BRANCH_HEADER": "HTTP_X_CURAVIA_BRANCH",
    "ORGANISATION_HEADER": "HTTP_X_CURAVIA_ORGANISATION",
    # Break-the-glass access window.
    "EMERGENCY_ACCESS_MINUTES": 60,
    # Signed URL lifetime for medical files, in seconds.
    "SIGNED_URL_TTL": 300,
    "PERMISSIONS_MATRIX": REPO_ROOT / "docs" / "permissions-matrix.yml",
    # Where the staff app is served. Used to build the links in emails, which
    # are opened outside the request that sent them, so they cannot be
    # relative and cannot be derived from a Host header a caller controls.
    "APP_URL": env("CURAVIA_APP_URL", "http://localhost:5173"),
}

# --------------------------------------------------------------------------
# Security headers
# --------------------------------------------------------------------------

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"

# Django 6 native Content-Security-Policy support.
SECURE_CSP = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:", "blob:"],
    "connect-src": ["'self'", "ws:", "wss:"],
    "frame-ancestors": ["'none'"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
}

PERMISSIONS_POLICY = {
    "geolocation": [],
    "microphone": ["self"],  # telemedicine
    "camera": ["self"],  # telemedicine
}

# --------------------------------------------------------------------------
# Logging — structured, so hosted log search is useful
# --------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "curavia.audit": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "curavia.tenancy": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
}

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "no-reply@curavia.ng")
