"""Spirit-module directory. Chat calls this instead of reading spirit rows."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.spirit_directory import DirectoryAnswer, DirectoryRow, classify_directory


class DirectoryReader(Protocol):
    async def lookup(self, viewer_id: UUID, spirit_id: UUID) -> DirectoryRow | None: ...


async def lookup_directory(
    reader: DirectoryReader, *, viewer_id: UUID, spirit_id: UUID
) -> DirectoryAnswer:
    row = await reader.lookup(viewer_id, spirit_id)
    return classify_directory(row, viewer_id=viewer_id)
