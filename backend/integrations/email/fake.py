"""Fake email provider."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from integrations.base import EmailResult


@dataclass
class FakeEmailProvider:
    name: str = "fake"
    outbox: list[dict[str, Any]] = field(default_factory=list)
    sent: ClassVar[list[dict[str, Any]]] = []

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
        message = {
            "to": to,
            "subject": subject,
            "html": html,
            "text": text,
            "reply_to": reply_to,
            "attachments": [name for name, _data, _mime in (attachments or [])],
        }
        self.outbox.append(message)
        type(self).sent.append(message)
        return EmailResult(provider=self.name, message_id=str(uuid.uuid4()), accepted=True)

    @classmethod
    def clear(cls) -> None:
        cls.sent.clear()
