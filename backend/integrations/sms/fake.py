"""Fake SMS provider. Records messages instead of sending them."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import ClassVar

from integrations.base import SmsResult


@dataclass
class FakeSmsProvider:
    """
    Used by the tests and by local development.

    ``outbox`` is a class attribute so a test can assert on it without holding
    a reference to the instance the code under test built.
    """

    name: str = "fake"
    outbox: list[dict[str, str]] = field(default_factory=list)

    #: Shared across instances, exactly like django.core.mail.outbox.
    sent: ClassVar[list[dict[str, str]]] = []

    def send(self, *, to: str, body: str, sender: str | None = None) -> SmsResult:
        message = {"to": to, "body": body, "sender": sender or "Curavia"}
        self.outbox.append(message)
        type(self).sent.append(message)
        return SmsResult(
            provider=self.name, message_id=str(uuid.uuid4()), accepted=True, cost_kobo=350
        )

    @classmethod
    def clear(cls) -> None:
        cls.sent.clear()
