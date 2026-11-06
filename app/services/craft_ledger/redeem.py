"""Redeem eligibility for the owner, and one operator mark that does not spend points."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.repositories.craft_redeem import REDEEM_POINTS, LedgerRedeem
from app.schemas.craft import CraftRedeemEligibility, CraftRedeemMark


class RedeemReader(Protocol):
    async def read_owner(self, owner_id: UUID) -> LedgerRedeem | None: ...


class RedeemBook(Protocol):
    async def claim(self, spirit_id: UUID, redeemed_at: datetime) -> LedgerRedeem | None: ...

    async def find(self, spirit_id: UUID) -> LedgerRedeem | None: ...


def _not_found() -> ApiError:
    return ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)


async def read_redeem_eligibility(
    reader: RedeemReader, *, owner_id: UUID
) -> CraftRedeemEligibility:
    row = await reader.read_owner(owner_id)
    if row is None:
        raise _not_found()
    redeemed = row.redeemed_at is not None
    return CraftRedeemEligibility(
        eligible=row.total_points >= REDEEM_POINTS and not redeemed,
        total_points=row.total_points,
        redeemed=redeemed,
    )


async def mark_spirit_redeemed(
    book: RedeemBook, *, spirit_id: UUID, redeemed_at: datetime
) -> CraftRedeemMark:
    written = await book.claim(spirit_id, redeemed_at)
    if written is not None and written.redeemed_at is not None:
        return CraftRedeemMark(
            spirit_id=spirit_id, redeemed=True, redeemed_at=written.redeemed_at
        )
    current = await book.find(spirit_id)
    if current is None:
        raise _not_found()
    if current.redeemed_at is None:
        raise ApiError(
            "INVALID_INPUT",
            public_error_message("INVALID_INPUT"),
            status_code=422,
        )
    return CraftRedeemMark(
        spirit_id=spirit_id, redeemed=True, redeemed_at=current.redeemed_at
    )
