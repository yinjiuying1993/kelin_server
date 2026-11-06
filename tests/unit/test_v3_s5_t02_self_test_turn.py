"""V3-S5-T02 one self-test turn uses the ticket snapshot and is not stored."""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.domain.craft_self_test import StoredTicket
from app.domain.craft_self_test.prompt import render_self_test_prompt
from app.integrations.bailian import BailianHttpAdapter
from app.services.craft_self_test.turn import send_self_test_turn

_OWNER = UUID("00000000-0000-4000-8000-000000000041")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000042")
_CRAFT = UUID("00000000-0000-4000-8000-000000000043")
_TICKET = UUID("00000000-0000-4000-8000-000000000044")
_NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)
_OLD = "进入时的正文"
_NEW = "保存后的新正文"


class _Store:
    def __init__(self) -> None:
        self.reads = 0
        self.writes = 0
        self.ticket = StoredTicket(
            ticket_id=_TICKET,
            spirit_id=_SPIRIT,
            craft_id=_CRAFT,
            skill_slug="pour-tea",
            snapshot_body=_OLD,
            expires_at=datetime(2026, 11, 4, 3, 30, tzinfo=UTC),
        )

    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        self.reads += 1
        return _SPIRIT if owner_id == _OWNER else None

    async def generated_skill(self, craft_id: UUID, spirit_id: UUID) -> object:
        del craft_id, spirit_id
        self.reads += 1
        return _NEW

    async def get_ticket(self, ticket_id: UUID) -> StoredTicket | None:
        self.reads += 1
        if ticket_id != self.ticket.ticket_id:
            return None
        return self.ticket

    async def insert_ticket(self, **fields: object) -> UUID:
        del fields
        self.writes += 1
        return _TICKET

    async def delete_expired(self, now: object) -> int:
        del now
        self.writes += 1
        return 0


class _Unit:
    def __init__(self, store: _Store) -> None:
        self.store = store
        self.depth = 0

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        self.depth += 1
        try:
            yield self.store
        finally:
            self.depth -= 1


class _Moderator:
    def __init__(self, unit: _Unit, decision: str) -> None:
        self.unit = unit
        self.decision_value = decision
        self.calls = 0

    async def decision(self, text: str) -> str:
        del text
        assert self.unit.depth == 0
        self.calls += 1
        return self.decision_value


class _Model:
    def __init__(self, unit: _Unit) -> None:
        self.unit = unit
        self.calls: list[dict[str, object]] = []

    async def reply(self, **kwargs: object) -> str:
        assert self.unit.depth == 0
        self.calls.append(kwargs)
        return "可以这样试。"


@pytest.mark.asyncio
async def test_blocked_text_does_not_call_the_model() -> None:
    unit = _Unit(_Store())
    model = _Model(unit)
    with pytest.raises(ApiError) as caught:
        await send_self_test_turn(
            unit,
            _Moderator(unit, "block"),
            model,
            owner_id=_OWNER,
            ticket_id=_TICKET,
            text="  伤害  ",
            dialogue=(("user", "上一句"),),
            now=_NOW,
        )
    assert caught.value.code == "MODERATION_REJECTED"
    assert model.calls == []
    assert unit.store.writes == 0


@pytest.mark.asyncio
async def test_reply_uses_the_ticket_snapshot_and_the_client_dialogue() -> None:
    unit = _Unit(_Store())
    model = _Model(unit)
    reply = await send_self_test_turn(
        unit,
        _Moderator(unit, "allow"),
        model,
        owner_id=_OWNER,
        ticket_id=_TICKET,
        text="  怎么倒  ",
        dialogue=(("spirit", "先烧水"), ("user", "  ")),
        now=_NOW,
    )
    assert reply == "可以这样试。"
    assert unit.store.writes == 0
    assert unit.store.reads == 2
    sent = model.calls[0]
    assert sent["snapshot_body"] == _OLD
    assert sent["text"] == "怎么倒"
    assert sent["dialogue"] == (("spirit", "先烧水"), ("user", "  "))
    instruction, user_text = render_self_test_prompt(
        snapshot_body=str(sent["snapshot_body"]),
        dialogue=sent["dialogue"],  # type: ignore[arg-type]
        text=str(sent["text"]),
    )
    assert _OLD in instruction
    assert _NEW not in instruction
    assert "spirit: 先烧水" in instruction
    assert user_text == "怎么倒"
    assert "hatched AI companion" not in instruction


@pytest.mark.asyncio
async def test_blank_text_is_rejected_before_the_model() -> None:
    unit = _Unit(_Store())
    model = _Model(unit)
    with pytest.raises(ApiError) as caught:
        await send_self_test_turn(
            unit,
            _Moderator(unit, "allow"),
            model,
            owner_id=_OWNER,
            ticket_id=_TICKET,
            text="   ",
            now=_NOW,
        )
    assert caught.value.code == "INVALID_INPUT"
    assert model.calls == []
    assert unit.store.reads == 0


def test_self_test_prompt_is_not_the_room_chat_prompt() -> None:
    source = inspect.getsource(BailianHttpAdapter.complete_self_test)
    assert "_chat_instruction" not in source
    assert "CHAT_PROMPT_VERSION" not in source
    turn = inspect.getsource(send_self_test_turn)
    for forbidden in ("ask_questions", "craft_ledgers", "help_count", "self-test/history"):
        assert forbidden not in turn
