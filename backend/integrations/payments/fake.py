"""
Fake payment provider.

Never contacts Paystack. ``verify`` reports a payment as successful only if
``create_intent`` was called for that reference, so a test cannot accidentally
assert on a payment that was never started.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from django.utils import timezone

from integrations.base import PaymentIntent, PaymentVerification


@dataclass
class FakePaymentProvider:
    name: str = "fake"
    intents: dict[str, int] = field(default_factory=dict)
    created: ClassVar[dict[str, int]] = {}

    def create_intent(
        self, *, amount_kobo: int, email: str, reference: str, metadata: dict[str, Any]
    ) -> PaymentIntent:
        self.intents[reference] = amount_kobo
        type(self).created[reference] = amount_kobo
        return PaymentIntent(
            provider=self.name,
            reference=reference,
            authorisation_url=f"https://example.invalid/pay/{reference}",
            amount_kobo=amount_kobo,
        )

    def verify(self, *, reference: str) -> PaymentVerification:
        amount = type(self).created.get(reference)
        return PaymentVerification(
            provider=self.name,
            reference=reference,
            paid=amount is not None,
            amount_kobo=amount or 0,
            paid_at=timezone.now() if amount is not None else None,
        )

    def verify_webhook(self, *, body: bytes, signature: str) -> bool:
        # The fake accepts only a signature the test set deliberately, so a
        # missing signature check in real code cannot pass the suite.
        return signature == "test-signature"

    @classmethod
    def clear(cls) -> None:
        cls.created.clear()
