"""V3-S7-T01 a later turn keeps the snapshot and does not rematch."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.domain.ask import prompt as ask_prompt
from app.domain.ask.prompt import render_ask_prompt
from app.repositories.ask_follow import FollowDialogue
from app.repositories.ask_turns import TurnClaim
from app.services.ask import follow as follow_module
from app.services.ask.first_reply import AnswerCallFailed
from app.services.ask.follow import send_follow_up

_ASKER = UUID("00000000-0000-4000-8000-000000000091")
_QUESTION_ID = UUID("00000000-0000-4000-8000-000000000092")
_CLIENT = UUID("00000000-0000-4000-8000-000000000093")
_AT = datetime(2026, 11, 4, 2, 50, tzinfo=UTC)
_LATER = datetime(2026, 11, 4, 3, 10, tzinfo=UTC)
_PRIOR = (("user", "谁会倒茶"), ("spirit", "先烧水。"))


def _dialogue(
    *,
    rated: bool = False,
    first_reply_at: datetime | None = _AT,
    turns: tuple[tuple[str, str], ...] = _PRIOR,
) -> FollowDialogue:
    return FollowDialogue(
        question_id=_QUESTION_ID,
        question_text="谁会倒茶",
        snapshot_detail="先烧水，再闷三分钟。",
        snapshot_reference_links=("https://tea.example/a",),
        first_reply_at=first_reply_at,
        rated=rated,
        turns=turns,  # type: ignore[arg-type]
    )


class _Dialogues:
    def __init__(self, dialogue: FollowDialogue | None) -> None:
        self.dialogue = dialogue
        self.loads = 0

    async def load(self, asker_user_id: UUID, question_id: UUID) -> FollowDialogue | None:
        del asker_user_id, question_id
        self.loads += 1
        return self.dialogue


class _Turns:
    def __init__(self, history_at: datetime | None = None) -> None:
        self.rows: dict[UUID, TurnClaim] = {}
        self.history_at = history_at
        self.model_gate = "closed"

    async def occupy(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
    ) -> TurnClaim:
        del question_id, asker_user_id
        current = self.rows.get(client_id)
        if current is None:
            claim = TurnClaim("owned", user_text=user_text)
            self.rows[client_id] = claim
            return claim
        if current.kind == "succeeded":
            return current
        if current.kind == "failed":
            claim = TurnClaim("owned", user_text=current.user_text)
            self.rows[client_id] = claim
            return claim
        return TurnClaim("busy")

    async def fail(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
    ) -> None:
        del question_id, asker_user_id
        self.rows[client_id] = TurnClaim("failed", user_text=user_text)

    async def succeed(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
        reply_text: str,
        answered_at: datetime,
    ) -> datetime:
        del asker_user_id
        self.rows[client_id] = TurnClaim("succeeded", reply_text=reply_text, user_text=user_text)
        return await self.mark_history(question_id, _ASKER, answered_at)

    async def mark_history(
        self, question_id: UUID, asker_user_id: UUID, answered_at: datetime
    ) -> datetime:
        del question_id, asker_user_id
        if self.history_at is None:
            self.history_at = answered_at
        return self.history_at


class _Model:
    def __init__(self, replies: list[str | Exception] | None = None) -> None:
        self._replies = replies or ["再闷一下。"]
        self.calls = 0
        self.seen: list[dict[str, object]] = []

    async def reply(self, **fields: object) -> str:
        self.calls += 1
        self.seen.append(fields)
        outcome = self._replies.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Moderator:
    def __init__(self, decision: str = "allow") -> None:
        self._decision = decision
        self.calls = 0

    async def decision(self, text: str) -> str:
        del text
        self.calls += 1
        return self._decision


@pytest.mark.asyncio
async def test_first_usable_reply_enters_history_without_rematch() -> None:
    dialogues = _Dialogues(_dialogue(first_reply_at=None, turns=()))
    turns = _Turns()
    model = _Model(["  先烧水。  "])
    opened = await send_follow_up(
        dialogues,
        turns,
        model,
        _Moderator(),
        asker_user_id=_ASKER,
        question_id=_QUESTION_ID,
        client_id=_CLIENT,
        text="  谁会倒茶  ",
        now=_AT,
    )
    assert opened.reply_text == "先烧水。"
    assert opened.first_reply_at == _AT
    assert turns.history_at == _AT
    assert model.calls == 1
    assert model.seen[0]["detail"] == "先烧水，再闷三分钟。"
    assert model.seen[0]["dialogue"] == ()
    assert "body" not in model.seen[0]
    again = await send_follow_up(
        dialogues,
        turns,
        model,
        _Moderator(),
        asker_user_id=_ASKER,
        question_id=_QUESTION_ID,
        client_id=_CLIENT,
        text="换一句",
        now=_LATER,
    )
    assert again.reply_text == "先烧水。"
    assert again.first_reply_at == _AT
    assert model.calls == 1


@pytest.mark.asyncio
async def test_later_turn_uses_the_snapshot_dialogue_and_keeps_history() -> None:
    dialogues = _Dialogues(_dialogue())
    turns = _Turns(history_at=_AT)
    model = _Model()
    client = UUID("00000000-0000-4000-8000-000000000094")
    opened = await send_follow_up(
        dialogues,
        turns,
        model,
        _Moderator(),
        asker_user_id=_ASKER,
        question_id=_QUESTION_ID,
        client_id=client,
        text="要闷多久",
        now=_LATER,
    )
    assert opened.first_reply_at == _AT
    assert model.seen[0]["question"] == "谁会倒茶"
    assert model.seen[0]["dialogue"] == _PRIOR
    assert model.seen[0]["text"] == "要闷多久"
    assert model.seen[0]["reference_links"] == ("https://tea.example/a",)


@pytest.mark.asyncio
async def test_rated_and_blocked_turns_do_not_call_the_model() -> None:
    kept = _Turns(history_at=_AT)
    kept.rows[_CLIENT] = TurnClaim("succeeded", reply_text="先烧水。", user_text="谁会倒茶")
    model = _Model()
    with pytest.raises(ApiError) as closed:
        await send_follow_up(
            _Dialogues(_dialogue(rated=True)),
            kept,
            model,
            _Moderator("block"),
            asker_user_id=_ASKER,
            question_id=_QUESTION_ID,
            client_id=UUID("00000000-0000-4000-8000-000000000095"),
            text="再问一次",
            now=_LATER,
        )
    assert closed.value.code == "ASK_CLOSED"
    assert closed.value.message == "question is closed"
    assert closed.value.status_code == 409
    assert model.calls == 0
    assert kept.rows[_CLIENT].reply_text == "先烧水。"
    with pytest.raises(ApiError) as blocked:
        await send_follow_up(
            _Dialogues(_dialogue()),
            kept,
            model,
            _Moderator("block"),
            asker_user_id=_ASKER,
            question_id=_QUESTION_ID,
            client_id=UUID("00000000-0000-4000-8000-000000000096"),
            text="不能发的话",
            now=_LATER,
        )
    assert blocked.value.code == "MODERATION_REJECTED"
    assert blocked.value.message == "moderation rejected"
    assert model.calls == 0
    assert kept.rows[_CLIENT].kind == "succeeded"
    assert UUID("00000000-0000-4000-8000-000000000096") not in kept.rows


@pytest.mark.asyncio
async def test_same_key_does_not_call_the_model_twice() -> None:
    dialogues = _Dialogues(_dialogue(first_reply_at=None, turns=()))
    turns = _Turns()
    pending = UUID("00000000-0000-4000-8000-000000000097")
    turns.rows[pending] = TurnClaim("owned", user_text="谁会倒茶")
    model = _Model()
    with pytest.raises(ApiError) as caught:
        await send_follow_up(
            dialogues,
            turns,
            model,
            _Moderator(),
            asker_user_id=_ASKER,
            question_id=_QUESTION_ID,
            client_id=pending,
            text="谁会倒茶",
            now=_AT,
        )
    assert caught.value.code == "IDEMPOTENCY_IN_PROGRESS"
    assert caught.value.status_code == 409
    assert caught.value.retryable is True
    assert model.calls == 0
    assert turns.history_at is None


@pytest.mark.asyncio
async def test_failed_turn_can_retry_without_entering_history_early() -> None:
    dialogues = _Dialogues(_dialogue(first_reply_at=None, turns=()))
    turns = _Turns()
    model = _Model([AnswerCallFailed(), "可以再试。"])
    with pytest.raises(ApiError) as missed:
        await send_follow_up(
            dialogues,
            turns,
            model,
            _Moderator(),
            asker_user_id=_ASKER,
            question_id=_QUESTION_ID,
            client_id=_CLIENT,
            text="谁会倒茶",
            now=_AT,
        )
    assert missed.value.code == "DEPENDENCY_UNAVAILABLE"
    assert missed.value.retryable is True
    assert turns.history_at is None
    assert turns.rows[_CLIENT].kind == "failed"
    retried = await send_follow_up(
        dialogues,
        turns,
        model,
        _Moderator(),
        asker_user_id=_ASKER,
        question_id=_QUESTION_ID,
        client_id=_CLIENT,
        text="谁会倒茶",
        now=_LATER,
    )
    assert retried.reply_text == "可以再试。"
    assert retried.first_reply_at == _LATER
    assert model.calls == 2
    assert len(turns.rows) == 1


def test_follow_up_does_not_open_another_match_channel() -> None:
    source = inspect.getsource(follow_module)
    for forbidden in (
        "match_published_skill",
        "complete_match",
        "craft_skills",
        "snapshot_body",
        "help_count",
        "craft_ledgers",
    ):
        assert forbidden not in source
    rendered = inspect.getsource(ask_prompt)
    assert "医疗、法律、投资" in rendered
    assert "match_published_skill" not in inspect.getsource(render_ask_prompt)
