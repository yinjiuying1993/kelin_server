"""The owner's helped row. One ledger total, not one row per dialogue."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

HELPED_SQL = """
SELECT spirit.name AS spirit_name, ledger.help_count
FROM public.spirits spirit
LEFT JOIN public.craft_ledgers ledger ON ledger.spirit_id = spirit.id
WHERE spirit.user_id = :owner_id
"""


@dataclass(frozen=True)
class HelpedLedger:
    spirit_name: str
    help_count: int


class SqlHelpedLedger:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read(self, owner_id: UUID) -> HelpedLedger | None:
        result = await self._session.execute(text(HELPED_SQL), {"owner_id": owner_id})
        row = result.one_or_none()
        if row is None:
            return None
        count = 0 if row.help_count is None else int(row.help_count)
        return HelpedLedger(str(row.spirit_name), count)
