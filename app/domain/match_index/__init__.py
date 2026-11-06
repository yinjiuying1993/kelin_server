"""Match index calls used by craft. The index stores no skill body."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.match_index.rows import MatchIndexError

__all__ = ["MatchIndex", "MatchIndexError"]


class MatchIndex(Protocol):
    async def stage(
        self,
        *,
        craft_id: UUID,
        spirit_id: UUID,
        skill_slug: str,
        title: str,
        keywords: tuple[str, ...],
    ) -> None: ...

    async def activate(self, *, craft_id: UUID) -> None: ...

    async def withdraw(self, *, craft_id: UUID) -> None: ...

    async def remove_active(self, *, craft_id: UUID) -> None: ...
