"""Redeem eligibility and one redemption time. Points are not updated here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

REDEEM_POINTS = 1000

OWNER_SQL = """
SELECT ledger.total_points AS total_points, ledger.redeemed_at AS redeemed_at
FROM public.spirits spirit
LEFT JOIN public.craft_ledgers ledger ON ledger.spirit_id = spirit.id
WHERE spirit.user_id = :owner_id
"""

FIND_SQL = """
SELECT total_points, redeemed_at
FROM public.craft_ledgers
WHERE spirit_id = :spirit_id
"""

MARK_SQL = """
UPDATE public.craft_ledgers
SET redeemed_at = :redeemed_at
WHERE spirit_id = :spirit_id
  AND total_points >= 1000
  AND redeemed_at IS NULL
RETURNING total_points, redeemed_at
"""


@dataclass(frozen=True)
class LedgerRedeem:
    total_points: int
    redeemed_at: datetime | None


class SqlCraftRedeem:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read_owner(self, owner_id: UUID) -> LedgerRedeem | None:
        result = await self._session.execute(text(OWNER_SQL), {"owner_id": owner_id})
        row = result.one_or_none()
        if row is None:
            return None
        if row.total_points is None:
            return LedgerRedeem(0, None)
        return LedgerRedeem(int(row.total_points), row.redeemed_at)

    async def find(self, spirit_id: UUID) -> LedgerRedeem | None:
        result = await self._session.execute(text(FIND_SQL), {"spirit_id": spirit_id})
        row = result.one_or_none()
        if row is None:
            return None
        return LedgerRedeem(int(row.total_points), row.redeemed_at)

    async def claim(self, spirit_id: UUID, redeemed_at: datetime) -> LedgerRedeem | None:
        result = await self._session.execute(
            text(MARK_SQL),
            {"spirit_id": spirit_id, "redeemed_at": redeemed_at},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return LedgerRedeem(int(row.total_points), row.redeemed_at)
