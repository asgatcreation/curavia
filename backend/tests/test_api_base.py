"""
Every API view resolves the tenant scope.

This is a structural test, like the manager guard in ``conftest.py``, and it
exists because of a real bug that session-based testing could never have found.

Tenant scope was resolved in middleware, which reads ``request.user``. DRF
authenticates **inside the view**, so for a JWT caller every middleware sees
``AnonymousUser``, no scope was set, and the first tenant-scoped query raised
``TenantScopeMissing`` — a 500 on a request that should have worked. Under
session authentication it worked perfectly.

Resolution now lives in ``apps.core.api.base.TenantScopedMixin``. The risk that
replaces it is a view that forgets the base class: it would work in the browser
with a session and 500 for the real SPA. So that is checked here rather than
left to chance.
"""

from __future__ import annotations

import pytest
from django.urls import get_resolver

from apps.core.api.base import TenantScopedMixin

#: Views that legitimately run before anyone has a hospital. Each needs a
#: reason, so adding one is a deliberate act rather than a way to silence this.
UNSCOPED_VIEWS: dict[str, str] = {
    "SpectacularAPIView": "serves the OpenAPI schema; no user, no hospital",
    "SpectacularSwaggerView": "serves the API docs page",
    "SpectacularRedocView": "serves the API docs page",
    "TokenRefreshView": "exchanges a refresh token; the hospital is irrelevant",
    "APIRootView": "DRF's router index; returns route URLs only, never hospital data",
}


def _api_views() -> list[tuple[str, type]]:
    """Every view class serving a route under /api/v1/."""
    found: list[tuple[str, type]] = []

    def walk(patterns, prefix=""):
        for pattern in patterns:
            route = prefix + str(getattr(pattern, "pattern", ""))
            nested = getattr(pattern, "url_patterns", None)
            if nested is not None:
                walk(nested, route)
                continue
            callback = getattr(pattern, "callback", None)
            view_class = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
            if view_class is not None and route.startswith("api/v1/"):
                found.append((route, view_class))

    walk(get_resolver().url_patterns)
    return found


def test_there_are_api_views_to_check() -> None:
    """Guards the guard: an empty list would make every assertion below vacuous."""
    views = _api_views()
    assert len(views) >= 10, f"only found {len(views)} API views, expected at least 10"


@pytest.mark.parametrize(
    ("route", "view_class"),
    _api_views(),
    ids=[f"{route}::{cls.__name__}" for route, cls in _api_views()],
)
def test_api_view_resolves_tenant_scope(route: str, view_class: type) -> None:
    """
    The view inherits ``TenantScopedMixin``, or is explicitly exempt.

    Without it, the view works under session auth and 500s under JWT — a
    failure mode that only appears in production.
    """
    if view_class.__name__ in UNSCOPED_VIEWS:
        pytest.skip(f"{view_class.__name__}: {UNSCOPED_VIEWS[view_class.__name__]}")

    assert issubclass(view_class, TenantScopedMixin), (
        f"{view_class.__name__} serves /{route} but does not inherit "
        f"TenantScopedMixin, so it will not have a tenant scope when a JWT "
        f"client calls it. Base it on one of the classes in "
        f"apps/core/api/base.py, or add it to UNSCOPED_VIEWS with a reason."
    )


def test_the_mixin_sets_and_clears_the_scope_around_a_request() -> None:
    """
    The scope must not outlive the request.

    A contextvar left set would hand the next request on the same worker the
    previous caller's hospital, which is the exact leak the whole design is
    built to prevent.
    """
    import inspect

    source = inspect.getsource(TenantScopedMixin)
    # perform_authentication, not initial: DRF checks permissions inside
    # initial(), so resolving there leaves the permission classes reading an
    # empty tenant_permissions and refusing every caller.
    assert "def perform_authentication" in source, (
        "scope must be established in perform_authentication, between "
        "authentication and the permission check"
    )
    assert "def finalize_response" in source, "scope must be released after the response"
    assert "reset_scope" in source, "finalize_response must reset the contextvar"
    assert "finally" in source, "the reset must run even when the handler raised"


def test_middleware_leaves_the_api_alone() -> None:
    """
    Both layers resolving scope is how the two drift apart.

    The middleware handles Django-rendered pages; the API base handles the API.
    This asserts the middleware really does skip /api/.
    """
    from apps.core.middleware import SKIP_PREFIXES

    assert "/api/" in SKIP_PREFIXES
    assert "/healthz" in SKIP_PREFIXES


def test_both_paths_share_one_resolver() -> None:
    """
    The middleware and the API base must call the same function.

    Two resolvers would eventually disagree, and the one that drifted would be
    a tenant leak.
    """
    import inspect

    from apps.core import middleware
    from apps.core.api import base

    assert "from .scope import" in inspect.getsource(middleware).split("\n\n")[0] or (
        "resolve" in inspect.getsource(middleware)
    )
    assert "from apps.core.scope import" in inspect.getsource(base)
    assert base.resolve is middleware.resolve
