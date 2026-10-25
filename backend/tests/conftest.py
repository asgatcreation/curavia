"""
Shared fixtures.

One fixture does real work here: ``assert_no_tenant_leak`` is session-scoped
and checks, after the whole run, that nothing registered a tenant-scoped model
without the guard. The rest are convenience.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.apps import apps
from rest_framework.test import APIClient

from apps.core.models import TenantScoped
from apps.core.modules import SizeMode
from apps.core.tenancy import TenantManager, UnscopedManager

from .factories import Hospital, build_hospital, build_platform_staff


@pytest.fixture(autouse=True)
def _clear_integration_fakes() -> Iterator[None]:
    """
    Empty the fake providers' outboxes between tests.

    They keep class-level lists, like ``django.core.mail.outbox``, so without
    this a test could pass on a message a previous test sent.
    """
    from integrations.email.fake import FakeEmailProvider
    from integrations.payments.fake import FakePaymentProvider
    from integrations.sms.fake import FakeSmsProvider
    from integrations.storage.fake import FakeFileStorage
    from integrations.video.fake import FakeVideoProvider

    for provider in (
        FakeSmsProvider,
        FakeEmailProvider,
        FakePaymentProvider,
        FakeVideoProvider,
        FakeFileStorage,
    ):
        provider.clear()
    yield
    for provider in (
        FakeSmsProvider,
        FakeEmailProvider,
        FakePaymentProvider,
        FakeVideoProvider,
        FakeFileStorage,
    ):
        provider.clear()


@pytest.fixture(autouse=True)
def _reset_permission_cache() -> Iterator[None]:
    """
    Drop the per-user permission cache between tests.

    ``User.permission_codes`` caches on the instance, which is right inside one
    request and wrong across tests that change a role.
    """
    yield


@pytest.fixture
def hospital(db) -> Hospital:
    """Lifeline-style hospital: two branches, every role, everything enabled."""
    return build_hospital(name="Lifeline Test Hospital")


@pytest.fixture
def hospital_solo(db) -> Hospital:
    """
    A one-doctor practice.

    Separate from `hospital` because the module and template filtering only
    shows its teeth at the small end: a solo clinic must never be offered a
    ward, a theatre or a mortuary register.
    """
    return build_hospital(name="Solo Test Practice", size_mode=SizeMode.SOLO)


@pytest.fixture
def other_hospital(db) -> Hospital:
    """A second, unrelated hospital. The isolation suite attacks across these two."""
    return build_hospital(name="Rival Test Hospital")


@pytest.fixture
def platform_support(db):
    return build_platform_staff("platform_support")


@pytest.fixture
def platform_admin(db):
    return build_platform_staff("platform_admin")


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def as_staff(api: APIClient):
    """
    Return a helper that signs a user in and points the client at a branch.

    Uses ``force_authenticate`` because what is under test is authorisation,
    not the login flow — the login flow has its own tests.
    """

    def _sign_in(user, *, branch=None, organisation=None):
        api.force_authenticate(user=user)
        headers = {}
        if organisation is not None:
            headers["HTTP_X_CURAVIA_ORGANISATION"] = str(getattr(organisation, "pk", organisation))
        if branch is not None:
            headers["HTTP_X_CURAVIA_BRANCH"] = str(getattr(branch, "pk", branch))
        api.credentials(**headers)
        return api

    return _sign_in


def tenant_scoped_models() -> list[type]:
    """Every concrete model that inherits TenantScoped."""
    return [
        model
        for model in apps.get_models()
        if issubclass(model, TenantScoped) and not model._meta.abstract
    ]


@pytest.fixture(scope="session", autouse=True)
def assert_every_tenant_model_is_guarded() -> None:
    """
    A structural test disguised as a fixture.

    Runs once, before anything else, and fails the whole suite if a
    tenant-scoped model was declared without the scoped default manager or
    without ``base_manager_name`` pointing at the unscoped one. Getting either
    wrong would silently remove the protection from that model, and no
    behavioural test would necessarily notice.
    """
    problems: list[str] = []
    for model in tenant_scoped_models():
        default = model._meta.default_manager
        if not isinstance(default, TenantManager):
            problems.append(
                f"{model.__name__}: default manager is "
                f"{type(default).__name__}, expected TenantManager"
            )
        unscoped_manager = getattr(model, "unscoped", None)
        if not isinstance(unscoped_manager, UnscopedManager):
            problems.append(f"{model.__name__}: missing the `unscoped` manager")
        if model._meta.base_manager_name != "unscoped":
            problems.append(
                f"{model.__name__}: base_manager_name is "
                f"{model._meta.base_manager_name!r}, expected 'unscoped'"
            )
    if problems:
        raise AssertionError(
            "Tenant-scoped models are not correctly guarded:\n  - " + "\n  - ".join(problems)
        )
