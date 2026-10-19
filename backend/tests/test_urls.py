"""
Every named route reverses.

This exists because of a real mistake. The API was mounted with
``include((api_v1, "api"), namespace="v1")``, which produces the single
namespace ``v1`` rather than the nested ``api:v1`` the tests and the
permissions matrix referred to. Nine isolation tests failed in CI with
``NoReverseMatch``, and the cause was one line in ``config/urls.py`` — not the
tenant boundary those tests exist to protect.

These checks need no database and run in under a second, so a broken URL name
is caught before the suites that depend on it, and the failure says plainly
that the route is wrong rather than looking like a security regression.
"""

from __future__ import annotations

import pytest
from django.urls import NoReverseMatch, get_resolver, reverse

SAMPLE_UUID = "00000000-0000-0000-0000-000000000000"

#: Every route the test suite and docs/permissions-matrix.yml reverse by name.
NAMED_ROUTES = [
    "healthz",
    "readyz",
    "schema",
    "swagger-ui",
    "redoc",
    "api:v1:me",
    "api:v1:switch-branch",
    "api:v1:modules",
    "api:v1:permissions",
    "api:v1:branch-list",
    "api:v1:department-list",
    "api:v1:audit-list",
    "api:v1:audit-verify",
    "api:v1:login",
    "api:v1:login-mfa",
    "api:v1:logout",
    "api:v1:token-refresh",
    "api:v1:sessions",
    "api:v1:sessions-revoke",
    "marketing:home",
]

DETAIL_ROUTES = [
    ("api:v1:branch-detail", {"pk": SAMPLE_UUID}),
    ("api:v1:department-detail", {"pk": SAMPLE_UUID}),
    ("api:v1:audit-detail", {"pk": SAMPLE_UUID}),
    ("marketing:legal", {"page": "privacy"}),
]


@pytest.mark.parametrize("name", NAMED_ROUTES)
def test_named_route_reverses(name: str) -> None:
    assert reverse(name).startswith("/")


@pytest.mark.parametrize(("name", "kwargs"), DETAIL_ROUTES)
def test_detail_route_reverses(name: str, kwargs: dict[str, str]) -> None:
    assert reverse(name, kwargs=kwargs).startswith("/")


def test_api_lives_under_the_versioned_prefix() -> None:
    """
    The version belongs in the path, not only in the namespace.

    drf-spectacular's ``SCHEMA_PATH_PREFIX`` is ``/api/v1``, so a route that
    reversed to something else would silently drop out of the published schema
    and out of the generated TypeScript types.
    """
    for name in (n for n in NAMED_ROUTES if n.startswith("api:v1:")):
        assert reverse(name).startswith("/api/v1/"), f"{name} is outside /api/v1/"


def test_health_endpoints_are_not_under_the_api_prefix() -> None:
    """
    ``/healthz`` is what UptimeRobot hits every five minutes to stop Render's
    free tier sleeping, and what the container's HEALTHCHECK calls. Moving it
    under a versioned, middleware-covered prefix would break both.
    """
    assert reverse("healthz") == "/healthz"
    assert reverse("readyz") == "/readyz"


def test_every_api_route_is_reachable_by_name() -> None:
    """
    Nothing under /api/v1/ is anonymous.

    A route with no name cannot be reversed, so it cannot appear in the
    permissions matrix — which means nothing would ever assert who may call it.
    """
    resolver = get_resolver()
    unnamed: list[str] = []

    def walk(patterns, prefix=""):
        for pattern in patterns:
            route = prefix + str(getattr(pattern, "pattern", ""))
            nested = getattr(pattern, "url_patterns", None)
            if nested is not None:
                walk(nested, route)
            elif route.startswith("api/v1/") and not pattern.name:
                unnamed.append(route)

    walk(resolver.url_patterns)
    assert not unnamed, f"API routes with no name: {unnamed}"


def test_a_missing_namespace_fails_loudly() -> None:
    """
    Guards the guard: confirm NoReverseMatch is what a wrong name produces, so
    these tests would actually have caught the original mistake.
    """
    with pytest.raises(NoReverseMatch):
        reverse("api:v2:me")
    with pytest.raises(NoReverseMatch):
        reverse("v1:branch-list")
