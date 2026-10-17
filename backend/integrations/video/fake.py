"""Fake video provider. Mints obviously-fake tokens."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import ClassVar

from django.utils import timezone

from integrations.base import VideoRoom


@dataclass
class FakeVideoProvider:
    name: str = "fake"
    rooms: set[str] = field(default_factory=set)
    created: ClassVar[set[str]] = set()

    def create_room(self, *, room_name: str, ttl_seconds: int) -> VideoRoom:
        self.rooms.add(room_name)
        type(self).created.add(room_name)
        return VideoRoom(
            provider=self.name,
            room_name=room_name,
            join_token=f"fake-token.{room_name}",
            url=f"https://example.invalid/room/{room_name}",
            expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
        )

    def join_token(
        self, *, room_name: str, identity: str, display_name: str, can_publish: bool
    ) -> str:
        role = "publisher" if can_publish else "viewer"
        return f"fake-token.{room_name}.{identity}.{role}"

    def end_room(self, *, room_name: str) -> None:
        self.rooms.discard(room_name)
        type(self).created.discard(room_name)

    @classmethod
    def clear(cls) -> None:
        cls.created.clear()
