"""
Fake private file storage.

Keeps bytes in memory and hands out a signed-looking URL with an expiry, so
code that must only ever use short-lived links can be tested without a bucket.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar

from django.utils import timezone


@dataclass
class FakeFileStorage:
    name: str = "fake"
    files: dict[str, bytes] = field(default_factory=dict)
    stored: ClassVar[dict[str, bytes]] = {}

    def save(self, *, key: str, content: bytes, content_type: str = "") -> str:
        self.files[key] = content
        type(self).stored[key] = content
        return key

    def read(self, *, key: str) -> bytes:
        return type(self).stored[key]

    def delete(self, *, key: str) -> None:
        self.files.pop(key, None)
        type(self).stored.pop(key, None)

    def signed_url(self, *, key: str, ttl_seconds: int = 300) -> str:
        expires = int((timezone.now() + timedelta(seconds=ttl_seconds)).timestamp())
        signature = hashlib.sha256(f"{key}:{expires}".encode()).hexdigest()[:16]
        return f"https://files.example.invalid/{key}?expires={expires}&sig={signature}"

    @classmethod
    def clear(cls) -> None:
        cls.stored.clear()
