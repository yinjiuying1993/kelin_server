"""V3-S8-T04 redeem eligibility and a single operator mark that does not spend points."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.api.v1 import craft as craft_api
from app.api.v1 import ops
from app.api.v1.craft import router as craft_router
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.main import create_app
from app.repositories import craft_redeem as redeem_sql
from app.repositories.craft_redeem import LedgerRedeem
from app.schemas.craft import CraftRedeemEligibility
from app.services.craft_ledger.redeem import mark_spirit_redeemed, read_redeem_eligibility
from fastapi.testclient import TestClient

_OWNER = UUID("00000000-0000-4000-8000-0000000000e1")
_SPIRIT = UUID("00000000-0000-4000-8000-0000000000a8")
_AT = datetime(2026, 11, 3, 3, tzinfo=UTC)
_LATER = datetime(2026, 11, 3, 4, tzinfo=UTC)


class _OwnerLedger:
    def __init__(self, row: LedgerRedeem | None) -> None:
        self.row = row

    async def read_owner(self, owner_id: UUID) -> LedgerRedeem | None:
        del owner_id
        return self.row


class _Book:
    def __init__(self, row: LedgerRedeem | None) -> None:
        self.row = row
        self.writes = 0

    async def claim(self, spirit_id: UUID, redeemed_at: datetime) -> LedgerRedeem | None:
        del spirit_id
        row = self.row
        if row is None or row.total_points < 1000 or row.redeemed_at is not None:
            return None
        self.writes += 1
        self.row = LedgerRedeem(row.total_points, redeemed_at)
        return self.row

    async def find(self, spirit_id: UUID) -> LedgerRedeem | None:
        del spirit_id
        return self.row


@pytest.mark.asyncio
async def test_eligibility_follows_points_and_the_redemption_time() -> None:
    ready = await read_redeem_eligibility(_OwnerLedger(LedgerRedeem(1000, None)), owner_id=_OWNER)
    short = await read_redeem_eligibility(_OwnerLedger(LedgerRedeem(999, None)), owner_id=_OWNER)
    spent = await read_redeem_eligibility(_OwnerLedger(LedgerRedeem(1000, _AT)), owner_id=_OWNER)
    empty = await read_redeem_eligibility(_OwnerLedger(LedgerRedeem(0, None)), owner_id=_OWNER)
    assert ready == CraftRedeemEligibility(eligible=True, total_points=1000, redeemed=False)
    assert short.eligible is False and short.total_points == 999 and short.redeemed is False
    assert spent.eligible is False and spent.redeemed is True and spent.total_points == 1000
    assert empty.eligible is False
    assert set(ready.model_dump()) == {"eligible", "total_points", "redeemed"}


@pytest.mark.asyncio
async def test_missing_spirit_is_not_found() -> None:
    with pytest.raises(ApiError) as caught:
        await read_redeem_eligibility(_OwnerLedger(None), owner_id=_OWNER)
    assert caught.value.code == "NOT_FOUND"
    assert caught.value.message == "not found"
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_mark_writes_the_time_once_and_keeps_the_points() -> None:
    book = _Book(LedgerRedeem(1000, None))
    first = await mark_spirit_redeemed(book, spirit_id=_SPIRIT, redeemed_at=_AT)
    second = await mark_spirit_redeemed(book, spirit_id=_SPIRIT, redeemed_at=_LATER)
    assert first.redeemed is True
    assert first.redeemed_at == _AT
    assert second.redeemed_at == _AT
    assert book.writes == 1
    assert book.row is not None
    assert book.row.total_points == 1000


@pytest.mark.asyncio
async def test_short_ledger_and_missing_spirit_do_not_mark() -> None:
    short = _Book(LedgerRedeem(999, None))
    with pytest.raises(ApiError) as caught:
        await mark_spirit_redeemed(short, spirit_id=_SPIRIT, redeemed_at=_AT)
    assert caught.value.code == "INVALID_INPUT"
    assert caught.value.status_code == 422
    assert short.writes == 0
    assert short.row is not None
    assert short.row.total_points == 999
    missing = _Book(None)
    with pytest.raises(ApiError) as absent:
        await mark_spirit_redeemed(missing, spirit_id=_SPIRIT, redeemed_at=_AT)
    assert absent.value.code == "NOT_FOUND"
    assert missing.writes == 0


def test_ordinary_craft_routes_cannot_write_redemption() -> None:
    writes = [
        route
        for route in craft_router.routes
        if "POST" in getattr(route, "methods", set())
        and str(getattr(route, "path", "")).endswith("/redeem")
    ]
    assert writes == []
    source = inspect.getsource(craft_api)
    assert "mark_spirit_redeemed" not in source
    assert "SET redeemed_at" not in source
    client = TestClient(create_app(Settings(app_env="test")))
    response = client.get("/api/v1/craft/redeem")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_ops_route_stays_forbidden() -> None:
    with pytest.raises(ApiError) as caught:
        await ops.require_operator(CurrentUser(id=_OWNER))
    assert caught.value.code == "FORBIDDEN"
    assert caught.value.status_code == 403
    sql = inspect.getsource(redeem_sql)
    assert "SET redeemed_at" in sql
    assert "total_points >=" in sql
    for forbidden in ("total_points =", "help_count", "orders", "ask_questions"):
        assert forbidden not in sql
