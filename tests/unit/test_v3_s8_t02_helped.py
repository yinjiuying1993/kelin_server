"""V3-S8-T02 the helped row is the owner's ledger total."""

from __future__ import annotations

import inspect
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.repositories import helped as helped_sql
from app.repositories.helped import HelpedLedger
from app.schemas.ask import HelpedResult
from app.services.craft_ledger.helped import read_helped

_OWNER = UUID("00000000-0000-4000-8000-0000000000f1")
_OTHER = UUID("00000000-0000-4000-8000-0000000000f2")


class _Ledger:
    def __init__(self, rows: dict[UUID, HelpedLedger | None]) -> None:
        self.rows = rows

    async def read(self, owner_id: UUID) -> HelpedLedger | None:
        return self.rows.get(owner_id)


@pytest.mark.asyncio
async def test_positive_count_is_one_row_and_zero_is_empty() -> None:
    reader = _Ledger(
        {
            _OWNER: HelpedLedger("雾生", 2),
            _OTHER: HelpedLedger("别人", 9),
        }
    )
    shown = await read_helped(reader, owner_id=_OWNER)
    assert shown.item is not None
    assert shown.item.spirit_name == "雾生"
    assert shown.item.help_count == 2
    assert set(shown.model_dump()) == {"item"}
    assert set(shown.item.model_dump()) == {"spirit_name", "help_count"}
    assert "别人" not in shown.model_dump_json()
    empty = await read_helped(_Ledger({_OWNER: HelpedLedger("雾生", 0)}), owner_id=_OWNER)
    assert empty == HelpedResult(item=None)
    missing_ledger = await read_helped(
        _Ledger({_OWNER: HelpedLedger("雾生", 0)}), owner_id=_OWNER
    )
    assert missing_ledger.item is None


@pytest.mark.asyncio
async def test_missing_spirit_is_not_found() -> None:
    with pytest.raises(ApiError) as caught:
        await read_helped(_Ledger({}), owner_id=_OWNER)
    assert caught.value.code == "NOT_FOUND"
    assert caught.value.message == "not found"
    assert caught.value.status_code == 404


def test_helped_query_is_the_ledger_total() -> None:
    source = inspect.getsource(helped_sql)
    assert "craft_ledgers" in source
    assert "help_count" in source
    for forbidden in (
        "ask_questions",
        "ask_ratings",
        "craft_settlements",
        "craft_skills",
        "rank_name",
        "question_text",
        "snapshot_",
        "next_cursor",
        "title",
    ):
        assert forbidden not in source
    assert "next_cursor" not in HelpedResult.model_fields
