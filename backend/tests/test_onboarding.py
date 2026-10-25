"""
Sign-up, provisioning and email verification.

The emphasis here is on the two things that are expensive to get wrong: a
hospital that is only half created, and a public endpoint that tells the world
who has an account.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import Role, StaffMembership, User
from apps.accounts.role_templates import ROLE_TEMPLATES
from apps.core.landing import FALLBACK, landing_for
from apps.core.models import Branch, Department, Organisation, OrganisationStatus
from apps.core.modules import SizeMode
from apps.core.setup_steps import (
    REQUIRED_STEP_KEYS,
    SETUP_STEP_KEYS,
    completed_steps,
    mark_step,
    next_step,
    setup_is_complete,
)
from apps.core.tenancy import unscoped
from apps.onboarding.emails import make_verification_token, read_verification_token
from apps.onboarding.provisioning import (
    ProvisioningError,
    provision_hospital,
    unique_slug,
)
from apps.platformadmin.models import Plan, Subscription
from integrations.email.fake import FakeEmailProvider

pytestmark = pytest.mark.django_db

GOOD_PASSWORD = "correct-horse-battery-staple-7"


@pytest.fixture(autouse=True)
def _plans() -> None:
    """Provisioning needs plans; a fresh test database has none."""
    from django.core.management import call_command

    call_command("seed_platform", verbosity=0)


@pytest.fixture(autouse=True)
def _empty_outbox() -> None:
    FakeEmailProvider.clear()


def signup_payload(**overrides) -> dict[str, str]:
    return {
        "email": "owner@example.test",
        "password": GOOD_PASSWORD,
        "first_name": "Folake",
        "last_name": "Ilesanmi",
        "hospital_name": "Oakfield Medical Centre",
        "size_mode": SizeMode.CLINIC,
        "state": "Lagos",
        **overrides,
    }


# --------------------------------------------------------------------------
# Provisioning
# --------------------------------------------------------------------------


def test_provisioning_creates_a_hospital_somebody_can_actually_use() -> None:
    """
    The whole point: after this call there is a person who can sign in and a
    hospital for them to sign in to.
    """
    result = provision_hospital(
        owner_email="Owner@Example.Test",
        owner_password=GOOD_PASSWORD,
        owner_first_name="Folake",
        owner_last_name="Ilesanmi",
        hospital_name="Oakfield Medical Centre",
    )

    assert result.owner.email == "owner@example.test"  # normalised
    assert result.owner.check_password(GOOD_PASSWORD)
    assert result.organisation.status == OrganisationStatus.TRIAL
    assert result.branch.is_main is True
    assert result.subscription.status == Subscription.Status.TRIALING
    assert result.subscription.trial_ends_at is not None

    with unscoped(reason="test: inspect what provisioning wrote"):
        assert Role.unscoped.filter(organisation=result.organisation).count() == len(ROLE_TEMPLATES)
        owner_role = Role.unscoped.get(organisation=result.organisation, slug="owner")
        assert owner_role.is_protected is True
        assert result.membership.roles.filter(pk=owner_role.pk).exists()
        assert list(result.membership.branches.all()) == [result.branch]
        assert Department.unscoped.filter(organisation=result.organisation).count() == 2


def test_the_owner_lands_pointing_at_their_own_hospital() -> None:
    """``last_organisation_id`` is what the scope resolver falls back to."""
    result = provision_hospital(
        owner_email="owner2@example.test",
        owner_password=GOOD_PASSWORD,
        owner_first_name="Yusuf",
        owner_last_name="Abubakar",
        hospital_name="Riverside Clinic",
    )
    result.owner.refresh_from_db()
    assert result.owner.last_organisation_id == result.organisation.pk
    assert result.owner.last_branch_id == result.branch.pk


def test_a_duplicate_email_is_refused_and_writes_nothing() -> None:
    """
    The transaction is the point.

    A failed second sign-up must not leave an orphan organisation behind —
    which is exactly what happens if the email check runs after the
    organisation is created.
    """
    provision_hospital(
        owner_email="taken@example.test",
        owner_password=GOOD_PASSWORD,
        owner_first_name="A",
        owner_last_name="B",
        hospital_name="First Hospital",
    )
    before = Organisation.objects.count()

    with pytest.raises(ProvisioningError):
        provision_hospital(
            owner_email="taken@example.test",
            owner_password=GOOD_PASSWORD,
            owner_first_name="C",
            owner_last_name="D",
            hospital_name="Second Hospital",
        )

    assert Organisation.objects.count() == before
    assert not Organisation.objects.filter(name="Second Hospital").exists()


def test_two_hospitals_with_the_same_name_both_get_an_address() -> None:
    """Nigeria has more than one St Mary's."""
    first = provision_hospital(
        owner_email="one@example.test",
        owner_password=GOOD_PASSWORD,
        owner_first_name="A",
        owner_last_name="B",
        hospital_name="St Mary's Hospital",
    )
    second = provision_hospital(
        owner_email="two@example.test",
        owner_password=GOOD_PASSWORD,
        owner_first_name="C",
        owner_last_name="D",
        hospital_name="St Mary's Hospital",
    )
    assert first.organisation.slug != second.organisation.slug
    assert first.organisation.slug == "st-marys-hospital"
    # Not "-2": the suffix must not count how many hospitals share the name.
    assert not second.organisation.slug.endswith("-2")


def test_a_name_of_pure_punctuation_still_gets_a_slug() -> None:
    assert unique_slug("!!!") == "hospital"


def test_a_hospital_cannot_see_another_hospitals_roles() -> None:
    """
    The tenant boundary holds across two freshly provisioned hospitals.

    Provisioning writes with the escape hatch, so this is the test that it
    writes to the right organisation each time rather than leaking.
    """
    first = provision_hospital(
        owner_email="a@example.test",
        owner_password=GOOD_PASSWORD,
        owner_first_name="A",
        owner_last_name="A",
        hospital_name="Alpha Hospital",
    )
    second = provision_hospital(
        owner_email="b@example.test",
        owner_password=GOOD_PASSWORD,
        owner_first_name="B",
        owner_last_name="B",
        hospital_name="Beta Hospital",
    )

    with unscoped(reason="test: count across both hospitals"):
        alpha_roles = set(
            Role.unscoped.filter(organisation=first.organisation).values_list("pk", flat=True)
        )
        beta_roles = set(
            Role.unscoped.filter(organisation=second.organisation).values_list("pk", flat=True)
        )
    assert alpha_roles.isdisjoint(beta_roles)

    # And the scoped manager, which is what every view uses, sees only one.
    from apps.core.tenancy import tenant_scope

    with tenant_scope(first.organisation.pk, first.branch.pk):
        assert Role.objects.count() == len(ROLE_TEMPLATES)
        assert not Branch.objects.filter(pk=second.branch.pk).exists()


def test_provisioning_fails_loudly_when_no_plans_exist() -> None:
    """A deployment that skipped `seed_platform` must not create half a tenant."""
    Subscription.objects.all().delete()
    Plan.objects.all().delete()

    with pytest.raises(ProvisioningError, match="seed_platform"):
        provision_hospital(
            owner_email="noplan@example.test",
            owner_password=GOOD_PASSWORD,
            owner_first_name="A",
            owner_last_name="B",
            hospital_name="No Plan Hospital",
        )
    assert not Organisation.objects.filter(name="No Plan Hospital").exists()


# --------------------------------------------------------------------------
# The sign-up endpoint
# --------------------------------------------------------------------------


def test_sign_up_creates_the_hospital_and_sends_one_email(api) -> None:
    response = api.post(reverse("api:v1:sign-up"), signup_payload(), format="json")

    assert response.status_code == 202
    assert Organisation.objects.filter(name="Oakfield Medical Centre").exists()
    assert len(FakeEmailProvider.sent) == 1
    assert FakeEmailProvider.sent[0]["to"] == ["owner@example.test"]
    assert "Oakfield Medical Centre" in FakeEmailProvider.sent[0]["subject"]


def test_sign_up_does_not_reveal_that_an_email_is_already_registered(api) -> None:
    """
    The two responses must be indistinguishable.

    Otherwise the form is a way to ask "is Dr X a Curavia customer?", which is
    commercially sensitive and, for a small practice, personally identifying.
    """
    first = api.post(reverse("api:v1:sign-up"), signup_payload(), format="json")
    FakeEmailProvider.clear()
    second = api.post(
        reverse("api:v1:sign-up"),
        signup_payload(hospital_name="Somewhere Else"),
        format="json",
    )

    assert first.status_code == second.status_code == 202
    assert first.data == second.data
    # And nothing was created the second time.
    assert not Organisation.objects.filter(name="Somewhere Else").exists()
    assert FakeEmailProvider.sent == []


def test_sign_up_rejects_a_weak_password(api) -> None:
    response = api.post(
        reverse("api:v1:sign-up"), signup_payload(password="password"), format="json"
    )
    assert response.status_code == 400
    # The error handler normalises field errors under "fields"; the message is
    # Django's own validator, so the API and `createsuperuser` agree.
    assert "password" in response.data["fields"]
    assert not User.objects.filter(email="owner@example.test").exists()


def test_sign_up_requires_a_hospital_name(api) -> None:
    response = api.post(reverse("api:v1:sign-up"), signup_payload(hospital_name=""), format="json")
    assert response.status_code == 400


def test_the_new_owner_is_not_yet_verified(api) -> None:
    api.post(reverse("api:v1:sign-up"), signup_payload(), format="json")
    owner = User.objects.get(email="owner@example.test")
    assert owner.email_verified_at is None


# --------------------------------------------------------------------------
# Email verification
# --------------------------------------------------------------------------


def test_a_valid_token_verifies_the_address(api) -> None:
    api.post(reverse("api:v1:sign-up"), signup_payload(), format="json")
    owner = User.objects.get(email="owner@example.test")

    response = api.post(
        reverse("api:v1:verify-email"),
        {"token": make_verification_token(str(owner.pk))},
        format="json",
    )

    assert response.status_code == 200
    owner.refresh_from_db()
    assert owner.email_verified_at is not None


def test_verifying_twice_is_not_an_error(api) -> None:
    api.post(reverse("api:v1:sign-up"), signup_payload(), format="json")
    owner = User.objects.get(email="owner@example.test")
    token = make_verification_token(str(owner.pk))

    assert (
        api.post(reverse("api:v1:verify-email"), {"token": token}, format="json").status_code == 200
    )
    owner.refresh_from_db()
    first_time = owner.email_verified_at

    assert (
        api.post(reverse("api:v1:verify-email"), {"token": token}, format="json").status_code == 200
    )
    owner.refresh_from_db()
    # Not re-stamped: the first confirmation is the one that happened.
    assert owner.email_verified_at == first_time


def test_a_forged_token_is_refused(api) -> None:
    response = api.post(
        reverse("api:v1:verify-email"), {"token": "not-a-real-token"}, format="json"
    )
    assert response.status_code == 400
    assert response.data["code"] == "bad_token"


def test_a_token_minted_for_another_purpose_is_refused() -> None:
    """
    The salt is what stops a signed value being reused across features.

    A token signed for an MFA challenge must not verify an email address.
    """
    from django.core.signing import TimestampSigner

    other = TimestampSigner(salt="curavia.mfa.challenge").sign("some-user-id")
    assert read_verification_token(other) is None


def test_resend_says_nothing_about_whether_the_account_exists(api) -> None:
    known = api.post(
        reverse("api:v1:verify-email-resend"), {"email": "nobody@example.test"}, format="json"
    )
    assert known.status_code == 202
    assert FakeEmailProvider.sent == []

    api.post(reverse("api:v1:sign-up"), signup_payload(), format="json")
    FakeEmailProvider.clear()

    real = api.post(
        reverse("api:v1:verify-email-resend"), {"email": "owner@example.test"}, format="json"
    )
    assert real.status_code == 202
    assert real.data == known.data
    assert len(FakeEmailProvider.sent) == 1


def test_resend_does_nothing_for_an_already_verified_address(api) -> None:
    api.post(reverse("api:v1:sign-up"), signup_payload(), format="json")
    owner = User.objects.get(email="owner@example.test")
    api.post(
        reverse("api:v1:verify-email"),
        {"token": make_verification_token(str(owner.pk))},
        format="json",
    )
    FakeEmailProvider.clear()

    api.post(reverse("api:v1:verify-email-resend"), {"email": "owner@example.test"}, format="json")
    assert FakeEmailProvider.sent == []


# --------------------------------------------------------------------------
# Role-based landing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("owner", "/group"),
        ("front_desk", "/"),
        ("cashier", "/billing"),
        ("doctor", "/consultation"),
        ("pharmacist", "/pharmacy"),
        ("lab_scientist", "/laboratory"),
        ("store_keeper", "/inventory"),
        ("auditor", "/audit"),
    ],
)
def test_each_role_lands_on_its_own_screen(slug: str, expected: str) -> None:
    from tests.factories import role_codes

    assert landing_for([slug], role_codes(slug)) == expected


def test_the_owner_wins_when_somebody_holds_several_roles() -> None:
    assert landing_for(["cashier", "owner"], []) == "/group"


def test_a_renamed_custom_role_still_lands_somewhere_useful() -> None:
    """
    A hospital that built its own role gets routed by what it can do.

    This is the case the whole permission fallback exists for: the slug means
    nothing to us, but "can dispense" does.
    """
    assert landing_for(["dispensary-assistant"], ["pharmacy.dispense"]) == "/pharmacy"


def test_a_role_that_grants_nothing_lands_on_an_explanation() -> None:
    assert landing_for(["brand-new-role"], []) == FALLBACK


def test_me_tells_the_client_where_to_go(api, hospital) -> None:
    user = hospital.staff("pharmacist")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )

    response = api.get(reverse("api:v1:me"))

    assert response.status_code == 200
    assert response.data["landing"] == "/pharmacy"
    assert response.data["email_verified"] is True
    assert "setup_complete" in response.data


def test_landing_follows_the_hospital_being_acted_for(api, hospital, other_hospital) -> None:
    """
    Somebody who is a pharmacist here and an owner elsewhere lands on the
    pharmacy when signed in here. The roles of the OTHER membership must not
    decide this one.
    """
    user = hospital.staff("pharmacist")
    with unscoped(reason="test: give this user a second membership elsewhere"):
        membership = StaffMembership.unscoped.create(
            organisation=other_hospital.organisation,
            user=user,
            default_branch=other_hospital.main_branch,
            job_title="Owner",
        )
        membership.branches.set([other_hospital.main_branch])
        membership.roles.set([other_hospital.roles["owner"]])

    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    assert api.get(reverse("api:v1:me")).data["landing"] == "/pharmacy"


# --------------------------------------------------------------------------
# Setup progress
# --------------------------------------------------------------------------


def test_a_brand_new_hospital_is_not_set_up(hospital) -> None:
    assert setup_is_complete(hospital.organisation) is False
    assert next_step(hospital.organisation) == SETUP_STEP_KEYS[0]


def test_marking_every_required_step_completes_the_setup(hospital) -> None:
    for key in REQUIRED_STEP_KEYS:
        mark_step(hospital.organisation, key)
    assert setup_is_complete(hospital.organisation) is True


def test_an_optional_step_can_be_skipped_and_is_recorded_as_skipped(hospital) -> None:
    mark_step(hospital.organisation, "payers", skipped=True)
    hospital.organisation.refresh_from_db()
    state = hospital.organisation.settings["setup"]
    assert state["skipped"] == ["payers"]
    assert "payers" in completed_steps(hospital.organisation)


def test_doing_a_step_after_skipping_it_clears_the_skip(hospital) -> None:
    mark_step(hospital.organisation, "wards", skipped=True)
    mark_step(hospital.organisation, "wards")
    hospital.organisation.refresh_from_db()
    state = hospital.organisation.settings["setup"]
    assert state["done"] == ["wards"]
    assert state["skipped"] == []


def test_an_unknown_step_is_rejected(hospital) -> None:
    with pytest.raises(ValueError, match="Unknown setup step"):
        mark_step(hospital.organisation, "not-a-step")


def test_somebody_with_no_hospital_is_never_sent_into_a_setup_wizard() -> None:
    assert setup_is_complete(None) is True
    assert next_step(None) == SETUP_STEP_KEYS[0]


def test_every_landing_path_has_a_screen_in_the_staff_app() -> None:
    """
    The server decides where each role lands, and the client has to have
    something there.

    This failed in the browser: a nurse signed in, the server sent her to
    /triage, and the router answered Not Found — because triage arrives at
    milestone 6 and nothing had said so. The fix was a "not built yet" screen
    that names what is coming; this is the test that keeps the two lists
    together.

    A text scan across the language boundary rather than a shared manifest.
    Crude, but the alternative is a generated file nobody reads, and the
    failure message below is clear enough to act on.
    """
    from pathlib import Path

    from django.conf import settings

    from apps.core.landing import FALLBACK, PERMISSION_LANDING, ROLE_LANDING

    staff_src = Path(settings.REPO_ROOT) / "frontend" / "staff" / "src"
    sources = (staff_src / "App.tsx").read_text(encoding="utf-8") + (
        staff_src / "screens" / "planned.ts"
    ).read_text(encoding="utf-8")

    wanted = {*ROLE_LANDING.values(), *(path for _code, path in PERMISSION_LANDING), FALLBACK}
    missing = sorted(path for path in wanted if f'"{path}"' not in sources)

    assert not missing, (
        "apps/core/landing.py sends people to paths the staff app has no screen "
        f"for: {missing}. Either build the screen, or add it to "
        "frontend/staff/src/screens/planned.ts so the person is told when it "
        "is coming instead of seeing Not Found."
    )
