"""
Outside services, behind interfaces.

Every third party Curavia talks to — SMS, email, payments, video, file storage
— is reached through one of the protocols below. Three reasons, in order of how
much they matter:

1. **Tests never touch a real provider.** Each protocol has a fake, and the
   test settings wire the fakes in. Nothing in the suite can send a text
   message to a real phone or charge a real card.
2. **Providers get swapped.** Termii is the Nigerian choice for SMS with Twilio
   as the fallback; LiveKit is today's video provider and may become a
   self-hosted LiveKit later. Each of those is a change to one file.
3. **Nothing is ever hard-coded to a vendor's shape.** A view asks for "send
   this patient a reminder", not for a Termii payload.

Protocols rather than abstract base classes: a provider only has to match the
shape, which keeps the fakes honest and short.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from django.conf import settings
from django.utils.module_loading import import_string

# --------------------------------------------------------------------------
# Value objects
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SmsResult:
    provider: str
    message_id: str
    accepted: bool
    #: Integer kobo, so it can be totalled without rounding error.
    cost_kobo: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class EmailResult:
    provider: str
    message_id: str
    accepted: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    provider: str
    reference: str
    #: Where to send the patient to pay.
    authorisation_url: str
    amount_kobo: int


@dataclass(frozen=True, slots=True)
class PaymentVerification:
    provider: str
    reference: str
    paid: bool
    amount_kobo: int
    paid_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VideoRoom:
    provider: str
    room_name: str
    #: Short-lived, minted by us, so our permission rules decide who may join.
    join_token: str
    url: str
    expires_at: datetime | None = None


# --------------------------------------------------------------------------
# Protocols
# --------------------------------------------------------------------------


@runtime_checkable
class SmsProvider(Protocol):
    name: str

    def send(self, *, to: str, body: str, sender: str | None = None) -> SmsResult:
        """Send one message. Must not raise on a provider error — return it."""
        ...


@runtime_checkable
class EmailProvider(Protocol):
    name: str

    def send(
        self,
        *,
        to: list[str],
        subject: str,
        html: str,
        text: str = "",
        reply_to: str | None = None,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> EmailResult:
        """
        Send one email.

        Brevo's HTTP API rather than SMTP, because Render's free tier blocks
        outbound SMTP — a lesson from CarHub.
        """
        ...


@runtime_checkable
class PaymentProvider(Protocol):
    name: str

    def create_intent(
        self, *, amount_kobo: int, email: str, reference: str, metadata: dict[str, Any]
    ) -> PaymentIntent: ...

    def verify(self, *, reference: str) -> PaymentVerification: ...

    def verify_webhook(self, *, body: bytes, signature: str) -> bool:
        """
        Confirm a webhook really came from the provider.

        Never trust a webhook body without this: anyone can POST "payment
        successful" to a public URL.
        """
        ...


@runtime_checkable
class VideoProvider(Protocol):
    name: str

    def create_room(self, *, room_name: str, ttl_seconds: int) -> VideoRoom: ...

    def join_token(
        self, *, room_name: str, identity: str, display_name: str, can_publish: bool
    ) -> str:
        """
        Mint a join token for one person.

        Issued by us after our own permission check, so a consultation room
        cannot be entered by someone who merely knows its name.
        """
        ...

    def end_room(self, *, room_name: str) -> None: ...


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

DEFAULT_BACKENDS: dict[str, str] = {
    "sms": "integrations.sms.fake.FakeSmsProvider",
    "email": "integrations.email.fake.FakeEmailProvider",
    "payments": "integrations.payments.fake.FakePaymentProvider",
    "video": "integrations.video.fake.FakeVideoProvider",
    "storage": "integrations.storage.fake.FakeFileStorage",
}


def get_provider(kind: str) -> Any:
    """
    Return the configured provider for ``kind``.

    Defaults to the fake. That is deliberate: a misconfigured deployment should
    fail to send a text message, not send one somewhere unexpected.
    """
    configured: dict[str, str] = getattr(settings, "INTEGRATION_BACKENDS", {})
    path = configured.get(kind) or DEFAULT_BACKENDS[kind]
    return import_string(path)()
