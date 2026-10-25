"""
Inviting a member of staff, and accepting the invitation.

**The token is never stored.** What goes in the database is its SHA-256
hash; the plaintext exists only in the email that was sent. A leaked database
row therefore cannot be used to join a hospital, which is the whole point of
hashing it — an invitation is an account-creation credential, and a row that
can be replayed is as good as a password.

That means a token cannot be shown again after it is created. "Resend" mints
a new one and invalidates the old, which is both simpler and more correct
than keeping the first alive: if somebody asks for a resend, the first link
has usually gone somewhere it should not have.

**Accepting is the only way to become staff.** No endpoint creates a
membership from nothing — a hospital's staff list can only grow through an
invitation it issued, and the invitation carries the roles and branches, so
the accepting person cannot choose their own access.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Invitation, Role, StaffMembership, User
from apps.core.models import Branch, Organisation
from apps.core.tenancy import tenant_scope
from integrations.base import get_provider

#: 32 bytes of randomness, URL-safe. Long enough that guessing is not a
#: strategy, short enough to survive an email client's line wrapping.
TOKEN_BYTES = 32

#: How long an invitation stays open. A week is long enough for somebody on
#: leave and short enough that a forgotten link in an inbox stops working.
INVITATION_DAYS = 7


class InvitationError(Exception):
    """Something was wrong with the request, not with us."""


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    invitation: Invitation
    #: Plaintext, returned once so it can be emailed. Never stored, never
    #: logged, never put in an API response.
    token: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def acceptance_url(token: str) -> str:
    base = getattr(settings, "CURAVIA", {}).get("APP_URL", "http://localhost:5173").rstrip("/")
    return f"{base}/accept-invitation?token={token}"


@transaction.atomic
def invite_staff(
    *,
    organisation: Organisation,
    email: str,
    role_ids: list[str],
    branch_ids: list[str],
    invited_by: User,
    first_name: str = "",
    last_name: str = "",
    job_title: str = "",
) -> IssuedInvitation:
    """
    Create an invitation and return it with its one-time token.

    Runs inside the caller's tenant scope, so the roles and branches below are
    looked up through the scoped managers: naming another hospital's role id
    finds nothing and is refused, rather than quietly granting access to a
    role that does not belong to this hospital.
    """
    address = email.strip().lower()
    if not address:
        raise InvitationError("An invitation needs an email address.")

    existing_member = StaffMembership.objects.filter(user__email=address, is_active=True).exists()
    if existing_member:
        raise InvitationError("That person is already on your staff list.")

    if Invitation.objects.filter(
        email=address, status=Invitation.Status.PENDING, expires_at__gt=timezone.now()
    ).exists():
        raise InvitationError(
            "An invitation is already open for that address. Resend it, or revoke it first."
        )

    roles = list(Role.objects.filter(pk__in=role_ids))
    if len(roles) != len(set(role_ids)) or not roles:
        raise InvitationError("Choose at least one role that belongs to this hospital.")

    branches = list(Branch.objects.filter(pk__in=branch_ids))
    if len(branches) != len(set(branch_ids)) or not branches:
        raise InvitationError("Choose at least one branch that belongs to this hospital.")

    token = secrets.token_urlsafe(TOKEN_BYTES)
    invitation = Invitation.objects.create(
        organisation=organisation,
        email=address,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        job_title=job_title.strip(),
        token_hash=hash_token(token),
        invited_by=invited_by,
        expires_at=timezone.now() + timedelta(days=INVITATION_DAYS),
    )
    invitation.roles.set(roles)
    invitation.branches.set(branches)

    return IssuedInvitation(invitation=invitation, token=token)


@transaction.atomic
def resend(invitation: Invitation) -> IssuedInvitation:
    """
    Mint a new token and extend the deadline.

    The old token stops working, deliberately. Somebody asking for a resend
    has usually lost track of where the first link went, and leaving two live
    credentials for one account is a worse answer than making them use the
    new one.
    """
    if invitation.status != Invitation.Status.PENDING:
        raise InvitationError("That invitation is no longer open.")

    token = secrets.token_urlsafe(TOKEN_BYTES)
    invitation.token_hash = hash_token(token)
    invitation.expires_at = timezone.now() + timedelta(days=INVITATION_DAYS)
    invitation.save(update_fields=["token_hash", "expires_at", "updated_at"])
    return IssuedInvitation(invitation=invitation, token=token)


def revoke(invitation: Invitation) -> Invitation:
    if invitation.status != Invitation.Status.PENDING:
        raise InvitationError("That invitation is no longer open.")
    invitation.status = Invitation.Status.REVOKED
    invitation.save(update_fields=["status", "updated_at"])
    return invitation


def find_open_invitation(token: str) -> Invitation | None:
    """
    Look an invitation up by its plaintext token.

    Deliberately NOT tenant-scoped, and the one place in this module that is
    not: somebody accepting an invitation is not signed in and has no
    hospital, so the token is the only thing that identifies which hospital
    they are joining. The lookup is by hash, which cannot be guessed, and it
    returns at most one row.
    """
    invitation = (
        Invitation.unscoped.filter(token_hash=hash_token(token))
        .select_related("organisation", "invited_by")
        .first()
    )
    if invitation is None or not invitation.is_open:
        return None
    return invitation


@transaction.atomic
def accept(*, token: str, password: str, first_name: str = "", last_name: str = "") -> User:
    """
    Turn an invitation into a member of staff.

    Creates the user if they are new, or attaches a membership if they
    already have a Curavia account at another hospital — a locum working at
    two places has one login, not two.

    The roles and branches come from the invitation, never from the request.
    Somebody accepting an invitation must not be able to choose what they can
    see.
    """
    invitation = find_open_invitation(token)
    if invitation is None:
        raise InvitationError("That invitation link is not valid, or it has expired.")

    organisation = invitation.organisation
    user = User.objects.filter(email=invitation.email).first()

    if user is None:
        # The invitation carries a name when whoever sent it typed one, and
        # the acceptance page asks for one when it did not. Refused here with
        # a sentence rather than letting the model's own validation surface
        # as a field error on a form that has no such field.
        given = (first_name or invitation.first_name).strip()
        family = (last_name or invitation.last_name).strip()
        if not given or not family:
            raise InvitationError("Please give your first name and surname.")

        user = User.objects.create_user(
            email=invitation.email,
            password=password,
            first_name=given,
            last_name=family,
            # The invitation went to this address and they opened it, which is
            # the same proof a verification email gives.
            email_verified_at=timezone.now(),
        )
    elif not user.has_usable_password():
        # An account created by an earlier invitation that was never
        # accepted. Setting the password here is what completes it.
        user.set_password(password)
        user.save(update_fields=["password"])

    # Everything below touches tenant-scoped rows — the invitation's own roles
    # and branches among them, because a related manager routes through the
    # scoped one. The scope is the hospital the invitation names, which is the
    # only hospital this function is entitled to write to.
    with tenant_scope(invitation.organisation_id):
        branches = list(invitation.branches.all())
        roles = list(invitation.roles.all())

        membership, created = StaffMembership.objects.get_or_create(
            organisation=organisation,
            user=user,
            defaults={"job_title": invitation.job_title},
        )
        membership.branches.set(branches)
        membership.roles.set(roles)
        if not created:
            # Re-invited after being deactivated: bring them back rather than
            # leaving a dead membership beside a live invitation.
            membership.is_active = True
            membership.deactivated_at = None
            membership.job_title = invitation.job_title or membership.job_title
            membership.save(update_fields=["is_active", "deactivated_at", "job_title"])
        if membership.default_branch_id is None and branches:
            membership.default_branch = branches[0]
            membership.save(update_fields=["default_branch"])

    invitation.status = Invitation.Status.ACCEPTED
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_at", "updated_at"])

    user.last_organisation_id = organisation.pk
    user.last_branch_id = membership.default_branch_id
    user.save(update_fields=["last_organisation_id", "last_branch_id"])
    return user


def send_invitation_email(issued: IssuedInvitation, *, hospital_name: str, inviter: str) -> None:
    """
    The email. Short, and it says who invited them and what happens next.

    Being asked to set a password by an unfamiliar product is exactly the
    shape of a phishing email, so the message names the hospital and the
    person — the two things a recipient can check against what they already
    know.
    """
    invitation = issued.invitation
    link = acceptance_url(issued.token)
    greeting = f"Hello {invitation.first_name}," if invitation.first_name else "Hello,"
    # Scoped: `invitation.roles` is a related manager over a tenant-scoped
    # model, and this is called from a resend as well as from the signed-in
    # invite view, so it cannot assume a scope is already active.
    with tenant_scope(invitation.organisation_id):
        roles = ", ".join(role.name for role in invitation.roles.all())

    text = (
        f"{greeting}\n\n"
        f"{inviter} has invited you to join {hospital_name} on Curavia"
        f"{f' as {roles}' if roles else ''}.\n\n"
        f"Set your password and sign in here:\n\n{link}\n\n"
        f"The link works for {INVITATION_DAYS} days. You will be asked to set up "
        "two-factor authentication, which Curavia requires for every member of "
        "staff.\n\n"
        "If you were not expecting this, ignore the email and nothing will happen.\n"
    )
    html = (
        f"<p>{greeting}</p>"
        f"<p><strong>{inviter}</strong> has invited you to join "
        f"<strong>{hospital_name}</strong> on Curavia"
        f"{f' as {roles}' if roles else ''}.</p>"
        f'<p><a href="{link}">Set my password and sign in</a></p>'
        f"<p>The link works for {INVITATION_DAYS} days. You will be asked to set up "
        "two-factor authentication, which Curavia requires for every member of staff.</p>"
        "<p>If you were not expecting this, ignore the email and nothing will happen.</p>"
    )

    get_provider("email").send(
        to=[invitation.email],
        subject=f"{inviter} has invited you to {hospital_name}",
        html=html,
        text=text,
    )
