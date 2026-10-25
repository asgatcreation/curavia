"""
The emails onboarding sends.

Tokens are signed, not stored. ``TimestampSigner`` produces a token that
carries its own expiry and cannot be forged without the secret key, so there
is no verification table to keep, to clean up, or to leak. The cost is that a
token cannot be revoked individually — acceptable here, because the only thing
a verification token does is mark an address as reachable, and it expires in a
day.

The signer is salted per purpose. A token minted to verify an email address
must not be accepted anywhere else, and a shared salt would make every signed
value in the system interchangeable.
"""

from __future__ import annotations

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

from integrations.base import get_provider

EMAIL_VERIFY_SALT = "curavia.onboarding.verify-email"
EMAIL_VERIFY_MAX_AGE = 60 * 60 * 24  # one day


def make_verification_token(user_id: str) -> str:
    return TimestampSigner(salt=EMAIL_VERIFY_SALT).sign(str(user_id))


def read_verification_token(token: str) -> str | None:
    """Return the user id, or None if the token is forged or stale."""
    try:
        return TimestampSigner(salt=EMAIL_VERIFY_SALT).unsign(token, max_age=EMAIL_VERIFY_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def verification_url(token: str) -> str:
    base = getattr(settings, "CURAVIA", {}).get("APP_URL", "http://localhost:5173").rstrip("/")
    return f"{base}/verify-email?token={token}"


def send_verification_email(*, to: str, first_name: str, hospital: str, token: str) -> None:
    """
    Plain, short, and it says what the link does.

    No marketing in a verification email: the one job is that somebody who
    just signed up recognises it in a crowded inbox and trusts it enough to
    click. The hospital name is in the subject for exactly that reason.
    """
    link = verification_url(token)
    greeting = f"Hello {first_name}," if first_name else "Hello,"

    text = (
        f"{greeting}\n\n"
        f"Confirm this email address to finish setting up {hospital} on Curavia:\n\n"
        f"{link}\n\n"
        "The link works for one day. If you did not create this account, ignore "
        "this message and nothing will happen.\n"
    )
    html = (
        f"<p>{greeting}</p>"
        f"<p>Confirm this email address to finish setting up "
        f"<strong>{hospital}</strong> on Curavia.</p>"
        f'<p><a href="{link}">Confirm my email address</a></p>'
        f"<p>The link works for one day. If you did not create this account, "
        f"ignore this message and nothing will happen.</p>"
    )

    get_provider("email").send(
        to=[to],
        subject=f"Confirm your email for {hospital}",
        html=html,
        text=text,
    )
