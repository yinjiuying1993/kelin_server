"""One spirit-chat socket per user. A new connection replaces the old one."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID


class Hub:
    def __init__(self) -> None:
        self._sockets: dict[UUID, Any] = {}
        self._lock = asyncio.Lock()

    async def attach(self, user_id: UUID, socket: Any) -> Any | None:
        async with self._lock:
            old = self._sockets.get(user_id)
            self._sockets[user_id] = socket
            if old is socket:
                return None
            return old

    async def detach(self, user_id: UUID, socket: Any) -> None:
        async with self._lock:
            if self._sockets.get(user_id) is socket:
                del self._sockets[user_id]

    def current(self, user_id: UUID) -> Any | None:
        return self._sockets.get(user_id)
