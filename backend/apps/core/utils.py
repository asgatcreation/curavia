"""
Small primitives the rest of Curavia is built on.

Two things here matter beyond convenience:

* ``uuid7`` — every primary key is a time-ordered UUID. Sequential integers in
  URLs leak how many patients a hospital has and let anyone walk the records by
  guessing; random UUID4s destroy index locality. UUIDv7 gives us neither
  problem: unguessable, but sortable by creation time.
* ``Money`` — every amount in the database is an integer number of kobo.
  Floats cannot represent ₦0.10 exactly, and a hospital that loses a kobo per
  transaction loses trust. Formatting happens only at the edge.
"""

from __future__ import annotations

import os
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

__all__ = ["KOBO_PER_NAIRA", "format_naira", "kobo_to_naira", "naira_to_kobo", "uuid7"]

KOBO_PER_NAIRA: Final = 100


def uuid7() -> uuid.UUID:
    """
    Return a UUID version 7 (RFC 9562): 48-bit Unix timestamp in milliseconds,
    then 74 bits of randomness, with the version and variant bits set.

    Python 3.13 has no ``uuid.uuid7``, and the one dependency that provides it
    is not worth adding for twelve lines.
    """
    unix_ms = int(time.time() * 1000) & 0xFFFF_FFFF_FFFF
    rand = int.from_bytes(os.urandom(10), "big")

    value = unix_ms << 80
    value |= 0x7 << 76  # version 7
    value |= ((rand >> 62) & 0xFFF) << 64  # rand_a: 12 bits
    value |= 0b10 << 62  # variant 0b10
    value |= rand & 0x3FFF_FFFF_FFFF_FFFF  # rand_b: 62 bits
    return uuid.UUID(int=value)


def naira_to_kobo(naira: str | int | float | Decimal) -> int:
    """
    Convert a naira amount to integer kobo, rounding half up at the kobo.

    ``float`` is accepted because form data arrives that way, but it is routed
    through ``Decimal(str(...))`` so 1500.50 does not become 1500.4999999.
    """
    # str() first for floats: Decimal(1500.50) is 1500.4999999999999545...,
    # which would quietly lose a kobo on roughly every tenth bill.
    amount = Decimal(str(naira)) if isinstance(naira, float) else Decimal(naira)
    return int((amount * KOBO_PER_NAIRA).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def kobo_to_naira(kobo: int) -> Decimal:
    """Convert integer kobo to an exact Decimal naira amount."""
    return (Decimal(kobo) / KOBO_PER_NAIRA).quantize(Decimal("0.01"))


def format_naira(kobo: int, *, symbol: str = "₦") -> str:
    """
    Render integer kobo for display: ``150050`` -> ``'₦1,500.50'``.

    Negative amounts are shown as ``'-₦1,500.50'``, which is what a cashier
    expects on a refund line.
    """
    sign = "-" if kobo < 0 else ""
    naira, remainder = divmod(abs(int(kobo)), KOBO_PER_NAIRA)
    return f"{sign}{symbol}{naira:,}.{remainder:02d}"
