"""
Staff invitations.

An invitation is an account-creation credential for a hospital, so the tests
that matter are the ones that try to abuse it: replaying a stored token,
accepting into a hospital you were not invited to, granting yourself roles the
invitation did not carry, and inviting somebody using another hospital's role
ids.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts import invitations as invites
from apps.accounts.models import Invitation, StaffMembership, User
from apps.core.tenancy import unscoped
from integrations.email.fake import FakeEmailProvider

pytestmark = [pytest.mark.django_db]

GOOD_PASSWORD = "correct-horse-battery-staple-7"


@pytest.fixture(autouse=True)
def _empty_outbox() -> None:
    FakeEmailProvider.clear()


@pytest.fixture
def api_as_owner(api, hospital):
    user = hospital.staff("owner")
    api.force_authenticate(user=user)
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    return api


def invite_payload(hospital, **overrides):
    return {
        "email": "newnurse@example.test",
        "first_name": "Amarachi",
        "last_name": "Eze",
        "job_title": "Staff nurse",
        "role_ids": [str(hospital.roles["nurse"].pk)],
        "branch_ids": [str(hospital.main_branch.pk)],
        **overrides,
    }


def issue(hospital, **kwargs) -> invites.IssuedInvitation:
    """Create an invitation directly, for tests that need the plaintext token."""
    owner = kwargs.pop("invited_by", None) or hospital.staff("owner")
    with hospital.scope():
        return invites.invite_staff(
            organisation=hospital.organisation,
            email=kwargs.pop("email", "newnurse@example.test"),
            role_ids=kwargs.pop("role_ids", [str(hospital.roles["nurse"].pk)]),
            branch_ids=kwargs.pop("branch_ids", [str(hospital.main_branch.pk)]),
            invited_by=owner,
            first_name=kwargs.pop("first_name", "Amarachi"),
            last_name=kwargs.pop("last_name", "Eze"),
            job_title=kwargs.pop("job_title", "Staff nurse"),
            **kwargs,
        )


# --------------------------------------------------------------------------
# The token
# --------------------------------------------------------------------------


def test_the_plaintext_token_is_never_stored(hospital) -> None:
    """
    The whole reason the column is called token_hash.

    Somebody with a copy of the database must not be able to join a hospital
    with it. They have the hash; the link that works was only ever in an
    email.
    """
    issued = issue(hospital)

    with hospital.scope():
        row = Invitation.objects.get(pk=issued.invitation.pk)
    assert row.token_hash != issued.token
    assert issued.token not in row.token_hash
    assert row.token_hash == invites.hash_token(issued.token)


def test_a_stored_hash_cannot_be_replayed_as_a_token(hospital) -> None:
    """The attack the hashing exists to stop, carried out."""
    issued = issue(hospital)
    with hospital.scope():
        stolen = Invitation.objects.get(pk=issued.invitation.pk).token_hash

    assert invites.find_open_invitation(stolen) is None
    with pytest.raises(invites.InvitationError):
        invites.accept(token=stolen, password=GOOD_PASSWORD)


def test_resending_invalidates_the_first_link(hospital) -> None:
    """
    Two live credentials for one account is a worse answer than making
    somebody use the newer link.
    """
    first = issue(hospital)
    with hospital.scope():
        second = invites.resend(first.invitation)

    assert invites.find_open_invitation(first.token) is None
    assert invites.find_open_invitation(second.token) is not None


def test_an_expired_invitation_is_refused(hospital) -> None:
    issued = issue(hospital)
    with hospital.scope():
        Invitation.objects.filter(pk=issued.invitation.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

    assert invites.find_open_invitation(issued.token) is None


def test_a_revoked_invitation_is_refused(hospital) -> None:
    issued = issue(hospital)
    with hospital.scope():
        invites.revoke(issued.invitation)

    assert invites.find_open_invitation(issued.token) is None


def test_an_invitation_cannot_be_accepted_twice(hospital) -> None:
    issued = issue(hospital)
    invites.accept(token=issued.token, password=GOOD_PASSWORD)

    with pytest.raises(invites.InvitationError):
        invites.accept(token=issued.token, password=GOOD_PASSWORD)


# --------------------------------------------------------------------------
# Accepting
# --------------------------------------------------------------------------


def test_accepting_without_a_name_anywhere_asks_for_one(hospital) -> None:
    """
    Better than a model validation error on a form with no name field.

    An invitation sent without a name is normal — the front desk often knows
    only an email — so the acceptance page asks, and the preview tells it to.
    """
    issued = issue(hospital, first_name="", last_name="")
    with pytest.raises(invites.InvitationError, match="first name and surname"):
        invites.accept(token=issued.token, password=GOOD_PASSWORD)

    user = invites.accept(
        token=issued.token, password=GOOD_PASSWORD, first_name="Ngozi", last_name="Bello"
    )
    assert user.full_name == "Ngozi Bello"


def test_the_preview_says_when_the_page_must_ask_for_a_name(api, hospital) -> None:
    named = issue(hospital, email="named@example.test")
    assert (
        api.get(reverse("api:v1:invitation-preview"), {"token": named.token}).data["needs_name"]
        is False
    )

    nameless = issue(hospital, email="nameless@example.test", first_name="", last_name="")
    assert (
        api.get(reverse("api:v1:invitation-preview"), {"token": nameless.token}).data["needs_name"]
        is True
    )


def test_accepting_creates_a_working_member_of_staff(hospital) -> None:
    issued = issue(hospital)
    user = invites.accept(token=issued.token, password=GOOD_PASSWORD)

    assert user.email == "newnurse@example.test"
    assert user.check_password(GOOD_PASSWORD)
    # The invitation arrived at this address and they opened it, which is the
    # same proof a verification email gives.
    assert user.email_verified_at is not None

    with hospital.scope():
        membership = StaffMembership.objects.get(user=user)
        assert membership.is_active is True
        assert [role.slug for role in membership.roles.all()] == ["nurse"]
        assert list(membership.branches.all()) == [hospital.main_branch]
        assert membership.default_branch_id == hospital.main_branch.pk
        assert membership.job_title == "Staff nurse"


def test_the_roles_come_from_the_invitation_not_the_request(api, hospital) -> None:
    """
    The escalation this flow exists to prevent.

    Somebody accepting an invitation must not be able to name their own
    roles. The acceptance endpoint takes a token and a password; there is
    nowhere to put a role id, and sending one changes nothing.
    """
    issued = issue(hospital)

    response = api.post(
        reverse("api:v1:invitation-accept"),
        {
            "token": issued.token,
            "password": GOOD_PASSWORD,
            # Not fields the serialiser knows. Sent anyway, as an attacker
            # would.
            "role_ids": [str(hospital.roles["owner"].pk)],
            "roles": ["owner"],
            "is_platform_staff": True,
        },
        format="json",
    )

    assert response.status_code == 200
    user = User.objects.get(email="newnurse@example.test")
    assert user.is_platform_staff is False
    with hospital.scope():
        membership = StaffMembership.objects.get(user=user)
        assert [role.slug for role in membership.roles.all()] == ["nurse"]


def test_a_locum_at_two_hospitals_has_one_account(hospital, other_hospital) -> None:
    """
    Two memberships, one login. A doctor working Tuesdays at one hospital and
    Thursdays at another should not need two passwords, and the branch
    switcher is what moves them between the two.
    """
    first = issue(hospital, email="locum@example.test")
    user = invites.accept(token=first.token, password=GOOD_PASSWORD)

    second = issue(
        other_hospital,
        email="locum@example.test",
        role_ids=[str(other_hospital.roles["doctor"].pk)],
        branch_ids=[str(other_hospital.main_branch.pk)],
    )
    again = invites.accept(token=second.token, password="a-different-one-entirely-9")

    assert again.pk == user.pk
    assert User.objects.filter(email="locum@example.test").count() == 1
    with unscoped(reason="test: count memberships across both hospitals"):
        assert StaffMembership.unscoped.filter(user=user).count() == 2
    # The second acceptance must not have changed the password of an account
    # that already had one — that would be account takeover by invitation.
    again.refresh_from_db()
    assert again.check_password(GOOD_PASSWORD)


def test_re_inviting_a_deactivated_member_brings_them_back(hospital) -> None:
    issued = issue(hospital)
    user = invites.accept(token=issued.token, password=GOOD_PASSWORD)

    with hospital.scope():
        membership = StaffMembership.objects.get(user=user)
        membership.is_active = False
        membership.deactivated_at = timezone.now()
        membership.save(update_fields=["is_active", "deactivated_at"])

    second = issue(hospital)
    invites.accept(token=second.token, password=GOOD_PASSWORD)

    with hospital.scope():
        membership.refresh_from_db()
        assert membership.is_active is True
        assert membership.deactivated_at is None


# --------------------------------------------------------------------------
# Issuing, through the API
# --------------------------------------------------------------------------


def test_an_owner_can_invite_and_one_email_goes_out(api_as_owner, hospital) -> None:
    response = api_as_owner.post(
        reverse("api:v1:invitation-list"), invite_payload(hospital), format="json"
    )

    assert response.status_code == 201
    assert response.data["email"] == "newnurse@example.test"
    assert len(FakeEmailProvider.sent) == 1
    sent = FakeEmailProvider.sent[0]
    assert sent["to"] == ["newnurse@example.test"]
    assert hospital.organisation.name in sent["subject"]


def test_the_token_never_appears_in_an_api_response(api_as_owner, hospital) -> None:
    """
    It is emailed, and that is the only place it exists.

    An API that returned it would let anybody who can invite staff mint a
    working link without the recipient ever seeing an email — which is how an
    invitation becomes a way to create an account you control.
    """
    response = api_as_owner.post(
        reverse("api:v1:invitation-list"), invite_payload(hospital), format="json"
    )
    body = str(response.data)
    assert "token" not in body
    assert "token_hash" not in response.data


def test_a_cashier_cannot_invite_staff(api, hospital) -> None:
    api.force_authenticate(user=hospital.staff("cashier"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    response = api.post(reverse("api:v1:invitation-list"), invite_payload(hospital), format="json")
    assert response.status_code == 403
    assert not Invitation.unscoped.filter(email="newnurse@example.test").exists()


def test_another_hospitals_role_cannot_be_granted(api_as_owner, hospital, other_hospital) -> None:
    """
    The tenant boundary on a field the caller supplies.

    Role ids come from the request, so this is the shape an attacker would
    send: a real UUID that belongs to a different hospital.
    """
    response = api_as_owner.post(
        reverse("api:v1:invitation-list"),
        invite_payload(hospital, role_ids=[str(other_hospital.roles["owner"].pk)]),
        format="json",
    )

    assert response.status_code == 400
    assert not Invitation.unscoped.filter(email="newnurse@example.test").exists()
    assert FakeEmailProvider.sent == []


def test_another_hospitals_branch_cannot_be_granted(api_as_owner, hospital, other_hospital) -> None:
    response = api_as_owner.post(
        reverse("api:v1:invitation-list"),
        invite_payload(hospital, branch_ids=[str(other_hospital.main_branch.pk)]),
        format="json",
    )
    assert response.status_code == 400


def test_inviting_somebody_who_already_works_here_is_refused(api_as_owner, hospital) -> None:
    existing = hospital.staff("nurse")
    response = api_as_owner.post(
        reverse("api:v1:invitation-list"),
        invite_payload(hospital, email=existing.email),
        format="json",
    )
    assert response.status_code == 400
    assert "already on your staff list" in response.data["detail"]


def test_a_second_open_invitation_for_one_address_is_refused(api_as_owner, hospital) -> None:
    url = reverse("api:v1:invitation-list")
    assert api_as_owner.post(url, invite_payload(hospital), format="json").status_code == 201
    second = api_as_owner.post(url, invite_payload(hospital), format="json")
    assert second.status_code == 400
    assert "already open" in second.data["detail"]


def test_invitations_from_one_hospital_are_invisible_to_another(
    api, hospital, other_hospital
) -> None:
    issue(hospital, email="theirs@example.test")

    api.force_authenticate(user=other_hospital.staff("owner"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(other_hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(other_hospital.main_branch.pk),
    )
    response = api.get(reverse("api:v1:invitation-list"))

    assert response.status_code == 200
    assert [row["email"] for row in response.data] == []


# --------------------------------------------------------------------------
# The acceptance page's preview
# --------------------------------------------------------------------------


def test_the_preview_shows_what_the_holder_already_knows(api, hospital) -> None:
    issued = issue(hospital)
    response = api.get(reverse("api:v1:invitation-preview"), {"token": issued.token})

    assert response.status_code == 200
    assert response.data["hospital_name"] == hospital.organisation.name
    assert response.data["roles"] == [hospital.roles["nurse"].name]
    assert response.data["email"] == "newnurse@example.test"


def test_the_preview_refuses_a_token_that_is_not_real(api) -> None:
    response = api.get(reverse("api:v1:invitation-preview"), {"token": "not-a-token"})
    assert response.status_code == 404


def test_the_preview_needs_a_token_at_all(api) -> None:
    assert api.get(reverse("api:v1:invitation-preview")).status_code == 404


# --------------------------------------------------------------------------
# Staff list and deactivation
# --------------------------------------------------------------------------


def test_the_staff_list_shows_who_has_not_enrolled_in_mfa(api_as_owner, hospital) -> None:
    hospital.staff("doctor")
    response = api_as_owner.get(reverse("api:v1:staff-list"))

    assert response.status_code == 200
    assert len(response.data) >= 2
    assert all(row["has_mfa"] is False for row in response.data)


def test_the_staff_list_is_only_this_hospitals(api_as_owner, hospital, other_hospital) -> None:
    other_hospital.staff("doctor", email="theirs@example.test")
    response = api_as_owner.get(reverse("api:v1:staff-list"))
    assert "theirs@example.test" not in {row["email"] for row in response.data}


def test_deactivating_ends_access_immediately(api_as_owner, hospital) -> None:
    doctor = hospital.staff("doctor")
    with hospital.scope():
        membership = StaffMembership.objects.get(user=doctor)

    response = api_as_owner.post(
        reverse("api:v1:staff-deactivate", kwargs={"pk": str(membership.pk)})
    )

    assert response.status_code == 200
    membership.refresh_from_db()
    assert membership.is_active is False
    assert membership.deactivated_at is not None
    # Not deleted. Their notes and their audit trail stay attributed to them.
    assert User.objects.filter(pk=doctor.pk).exists()


def test_nobody_can_deactivate_themselves(api_as_owner, hospital) -> None:
    """An owner who did would lock the hospital out of its own settings."""
    owner = hospital.staff("owner", email="theowner@example.test")
    api_as_owner.force_authenticate(user=owner)
    with hospital.scope():
        membership = StaffMembership.objects.get(user=owner)

    response = api_as_owner.post(
        reverse("api:v1:staff-deactivate", kwargs={"pk": str(membership.pk)})
    )

    assert response.status_code == 400
    assert response.data["code"] == "self_deactivate"
    membership.refresh_from_db()
    assert membership.is_active is True


def test_a_doctor_cannot_deactivate_a_colleague(api, hospital) -> None:
    victim = hospital.staff("nurse")
    with hospital.scope():
        membership = StaffMembership.objects.get(user=victim)

    api.force_authenticate(user=hospital.staff("doctor"))
    api.credentials(
        HTTP_X_CURAVIA_ORGANISATION=str(hospital.organisation.pk),
        HTTP_X_CURAVIA_BRANCH=str(hospital.main_branch.pk),
    )
    response = api.post(reverse("api:v1:staff-deactivate", kwargs={"pk": str(membership.pk)}))

    assert response.status_code == 403
    membership.refresh_from_db()
    assert membership.is_active is True


def test_another_hospitals_membership_cannot_be_deactivated(
    api_as_owner, hospital, other_hospital
) -> None:
    victim = other_hospital.staff("nurse")
    with other_hospital.scope():
        membership = StaffMembership.objects.get(user=victim)

    response = api_as_owner.post(
        reverse("api:v1:staff-deactivate", kwargs={"pk": str(membership.pk)})
    )

    assert response.status_code == 404
    membership.refresh_from_db()
    assert membership.is_active is True


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------


def test_the_role_list_is_this_hospitals_own_copies(api_as_owner, hospital) -> None:
    response = api_as_owner.get(reverse("api:v1:role-list"))

    assert response.status_code == 200
    slugs = {row["slug"] for row in response.data}
    assert "owner" in slugs
    assert "nurse" in slugs
    owner_row = next(row for row in response.data if row["slug"] == "owner")
    assert owner_row["is_protected"] is True
    assert owner_row["permission_count"] > 50


def test_a_role_list_never_leaks_another_hospitals_roles(
    api_as_owner, hospital, other_hospital
) -> None:
    response = api_as_owner.get(reverse("api:v1:role-list"))
    ids = {row["id"] for row in response.data}
    assert str(other_hospital.roles["owner"].pk) not in ids
