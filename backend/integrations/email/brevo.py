"""
Brevo, over its HTTP API.

**The API rather than SMTP**, because Render's free tier blocks outbound SMTP
— a lesson from CarHub, and the reason ``EMAIL_PROVIDER`` exists at all.

Brevo's free tier sends 300 emails a day, which for a clinic doing forty
appointments is comfortable and costs nothing. That matters: it means the
whole reminder feature works on day one without the hospital buying anything,
and SMS can be switched on later when they decide it is worth the money.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from integrations.base import EmailResult
from integrations.http import post_json

API_URL = "https://api.brevo.com/v3/smtp/email"


@dataclass
class BrevoEmailProvider:
    name: str = "brevo"

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
        key = getattr(settings, "BREVO_API_KEY", "") or ""
        sender = getattr(settings, "DEFAULT_FROM_EMAIL", "") or ""
        if not key or not sender:
            # Said plainly rather than swallowed. A hospital that thinks it is
            # sending reminders and is not is worse off than one that knows it
            # is not, and this message ends up on the screen that shows why a
            # reminder failed.
            return EmailResult(
                provider=self.name,
                message_id="",
                accepted=False,
                error="Email is not set up yet: BREVO_API_KEY and DEFAULT_FROM_EMAIL are empty.",
            )

        payload: dict[str, Any] = {
            "sender": _sender(sender),
            "to": [{"email": address} for address in to],
            "subject": subject,
            "htmlContent": html,
        }
        if text:
            payload["textContent"] = text
        if reply_to:
            payload["replyTo"] = {"email": reply_to}
        if attachments:
            import base64

            payload["attachment"] = [
                {"name": name, "content": base64.b64encode(data).decode("ascii")}
                for name, data, _mime in attachments
            ]

        result = post_json(API_URL, payload, headers={"api-key": key})
        if not result.ok:
            return EmailResult(
                provider=self.name, message_id="", accepted=False, error=result.error
            )
        return EmailResult(
            provider=self.name,
            message_id=str(result.data.get("messageId") or uuid.uuid4()),
            accepted=True,
        )


def _sender(value: str) -> dict[str, str]:
    """Accepts either ``name@host`` or ``Name <name@host>``."""
    if "<" in value and ">" in value:
        name, _, rest = value.partition("<")
        return {"name": name.strip().strip('"'), "email": rest.rstrip(">").strip()}
    return {"email": value.strip()}
