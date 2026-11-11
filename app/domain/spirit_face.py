"""A spirit face after the spirit module has committed. No image body."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

FaceListener = Callable[["SpiritFace"], Awaitable[None]]
_listeners: list[FaceListener] = []


@dataclass(frozen=True)
class SpiritFace:
    spirit_id: UUID
    display_name: str
    avatar_url: str
    available: bool


def subscribe_spirit_face(listener: FaceListener) -> Callable[[], None]:
    _listeners.append(listener)

    def cancel() -> None:
        if listener in _listeners:
            _listeners.remove(listener)

    return cancel


async def publish_spirit_face(face: SpiritFace) -> None:
    for listener in tuple(_listeners):
        await listener(face)
