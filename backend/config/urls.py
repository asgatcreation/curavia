"""
URL map.

Three front doors:

* ``/``          the public marketing site, server-rendered Django templates
                 (chosen over a third React app for SEO and first paint —
                 docs/00-decisions.md §2)
* ``/api/v1/``   the JSON API the staff app and patient portal both use
* ``/admin/``    Django admin, for us during development only

``/healthz`` and ``/readyz`` sit outside everything, including the tenant
middleware, because a health check must answer before any hospital is known.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.core.api import views as core_views

api_v1 = [
    path("auth/", include("apps.accounts.api.urls")),
    path("", include("apps.core.api.urls")),
]

# Nested namespaces, so routes reverse as "api:v1:branch-list". Worth the
# extra line: when there is a v2, "api:v2:branch-list" slots in beside it
# without renaming every existing reverse() call.
api_versions = [
    path("v1/", include((api_v1, "v1"))),
]

urlpatterns = [
    # Liveness and readiness. No auth, no tenant, no database for /healthz.
    path("healthz", core_views.healthz, name="healthz"),
    path("readyz", core_views.readyz, name="readyz"),
    path("api/", include((api_versions, "api"))),
    # OpenAPI: the schema the frontend's TypeScript types are generated from.
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    # The marketing site is last so it can own the root without shadowing.
    path("", include("apps.marketing.urls")),
]

if settings.DEBUG:
    admin.site.site_header = "Curavia (development)"
