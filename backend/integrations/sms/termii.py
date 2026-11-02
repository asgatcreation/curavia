"""
Termii, the Nigerian SMS gateway.

**Unlike email, this costs money every time it runs** — a few naira per
message, more to a number on the Do-Not-Disturb list. That single fact shapes
the whole design here and in ``apps/scheduling/reminders.py``:

* Without an API key this provider **refuses and says so**, rather than
  pretending. A silent no-op would let a hospital believe its patients were
  being texted.
* Every send returns its cost in kobo, so the hospital can be shown what the
  month has cost rather than finding out from a balance alert.
* Nothing retries forever. A message that has failed twice is a message that
  will keep failing and keep charging.

Numbers are normalised to international format before sending, because
Nigerian numbers are written locally as 0803… and a gateway wants 234803….
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

from integrations.base import SmsResult
from integrations.http import post_json

API_URL = "https://api.ng.termii.com/api/sms/send"

#: What Termii charges per message page, in kobo. Used only to record an
#: estimate when the response does not carry a real one; the hospital's
#: actual balance is always the truth.
ESTIMATED_COST_KOBO = 350


@dataclass
class TermiiSmsProvider:
    name: str = "termii"

    def send(self, *, to: str, body: str, sender: str | None = None) -> SmsResult:
        key = getattr(settings, "TERMII_API_KEY", "") or ""
        from_id = sender or getattr(settings, "TERMII_SENDER_ID", "") or ""
        if not key or not from_id:
            return SmsResult(
                provider=self.name,
                message_id="",
                accepted=False,
                error=(
                    "Text messages are not set up yet: TERMII_API_KEY and "
                    "TERMII_SENDER_ID are empty."
                ),
            )

        number = normalise(to)
        if not number:
            return SmsResult(
                provider=self.name,
                message_id="",
                accepted=False,
                error=f"{to!r} is not a phone number this can send to.",
            )

        result = post_json(
            API_URL,
            {
                "to": number,
                "from": from_id,
                "sms": body,
                "type": "plain",
                "channel": "generic",
                "api_key": key,
            },
        )
        if not result.ok:
            return SmsResult(provider=self.name, message_id="", accepted=False, error=result.error)

        # Termii's send response does not price the message, so what is
        # recorded is the estimate. It is enough to show a hospital roughly
        # what a month costs; the account balance is always the real figure,
        # and nothing here pretends otherwise.
        return SmsResult(
            provider=self.name,
            message_id=str(result.data.get("message_id") or ""),
            accepted=True,
            cost_kobo=ESTIMATED_COST_KOBO,
        )


def normalise(number: str) -> str:
    """
    A Nigerian number in the form a gateway wants: ``2348031234567``.

    Written locally as 08031234567 and spoken as "oh eight oh three", so the
    leading zero has to be swapped for the country code rather than kept.
    Anything that is already international is left alone.
    """
    digits = re.sub(r"[^\d+]", "", number or "")
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("234"):
        return digits if 13 <= len(digits) <= 14 else ""
    if digits.startswith("0") and len(digits) == 11:
        return f"234{digits[1:]}"
    if len(digits) == 10:  # given without the leading zero
        return f"234{digits}"
    return ""
