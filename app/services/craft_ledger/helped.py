"""Read the owner's helped row from the spirit ledger."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.repositories.helped import HelpedLedger
from app.schemas.ask import HelpedResult, HelpedRow


class HelpedReader(Protocol):
    async def read(self, owner_id: UUID) -> HelpedLedger | None: ...


async def read_helped(reader: HelpedReader, *, owner_id: UUID) -> HelpedResult:
    row = await reader.read(owner_id)
    if row is None:
        raise ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)
    if row.help_count <= 0:
        return HelpedResult(item=None)
    return HelpedResult(item=HelpedRow(spirit_name=row.spirit_name, help_count=row.help_count))
