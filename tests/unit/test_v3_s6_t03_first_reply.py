"""V3-S6-T03 the first usable reply enters history. A miss does not."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.domain.ask.prompt import render_ask_prompt
from app.integrations.bailian import BailianHttpAdapter
from app.repositories import ask_turns as turn_sql
from app.repositories.ask import SkillSnapshot, StoredAsk
from app.repositories.ask_turns import StoredTurn
from app.services.ask import first_reply as first_reply_module
from app.services.ask.first_reply import AnswerCallFailed, submit_first_reply
from app.services.ask.match import MatchChoice

_ASKER = UUID("00000000-0000-4000-8000-000000000071")
_OWNER = UUID("00000000-0000-4000-8000-000000000072")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000073")
_CLIENT = UUID("00000000-0000-4000-8000-000000000074")
_QUESTION = "谁会倒茶"
_AT = datetime(2026, 11, 4, 2, 30, tzinfo=UTC)
_LATER = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)


def _choice() -> MatchChoice:
    return MatchChoice("pour-tea", _SPIRIT, "倒茶", ("待客",))


def _snapshot(detail: str = "先烧水，再闷三分钟。") -> SkillSnapshot:
    return SkillSnapshot(
        title="倒茶",
        keywords=("待客",),
        detail=detail,
        reference_links=("https://tea.example/a",),
        body="进入时的正文",
        points=5,
        spirit_owner_user_id=_OWNER,
    )


class _Dialogues:
    def __init__(self) -> None:
        self.copied = _snapshot()
        self.saved: StoredAsk | None = None
        self.snapshot_reads = 0
        self.inserts = 0

    async def find(self, asker_user_id: UUID, client_id: UUID) -> StoredAsk | None:
        del asker_user_id, client_id
        return self.saved

    async def insert(self, **fields: object) -> UUID:
        self.inserts += 1
        self.saved = StoredAsk(
            question_id=UUID("00000000-0000-4000-8000-000000000075"),
            question_text=str(fields["question_text"]),
            snapshot_title=str(fields["snapshot_title"]),
            snapshot_rank_name=fields["snapshot_rank_name"],  # type: ignore[arg-type]
            snapshot_detail=str(fields["snapshot_detail"]),
            snapshot_reference_links=fields["snapshot_reference_links"],  # type: ignore[arg-type]
            snapshot_body=str(fields["snapshot_body"]),
            first_reply_at=None,
        )
        return self.saved.question_id

    async def official(self) -> None:
        return None

    async def snapshot(self, skill_slug: str, spirit_id: UUID) -> SkillSnapshot | None:
        del skill_slug, spirit_id
        self.snapshot_reads += 1
        return self.copied


class _Matcher:
    def __init__(self, choice: MatchChoice | None) -> None:
        self.choice = choice
        self.calls = 0

    async def match(self, question: str) -> MatchChoice | None:
        del question
        self.calls += 1
        return self.choice


class _Turns:
    def __init__(self) -> None:
        self.row: StoredTurn | None = None
        self.history_at: datetime | None = None
        self.rows = 0

    async def find(
        self, question_id: UUID, asker_user_id: UUID, client_id: UUID
    ) -> StoredTurn | None:
        del question_id, asker_user_id, client_id
        return self.row

    async def fail(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
    ) -> None:
        del question_id, asker_user_id, client_id, user_text
        if self.row is None:
            self.rows += 1
        if self.row is None or self.row.status != "succeeded":
            self.row = StoredTurn(None, "failed")

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
        del user_text
        if self.row is None:
            self.rows += 1
        if self.row is None or self.row.status != "succeeded":
            self.row = StoredTurn(reply_text, "succeeded")
        return await self.mark_history(question_id, asker_user_id, answered_at)

    async def mark_history(
        self, question_id: UUID, asker_user_id: UUID, answered_at: datetime
    ) -> datetime:
        del question_id, asker_user_id
        if self.history_at is None:
            self.history_at = answered_at
        return self.history_at


class _Model:
    def __init__(self, replies: list[str | Exception]) -> None:
        self._replies = replies
        self.calls = 0
        self.seen: list[dict[str, object]] = []

    async def reply(self, **fields: object) -> str:
        self.calls += 1
        self.seen.append(fields)
        outcome = self._replies.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_usable_reply_enters_history_once() -> None:
    dialogues = _Dialogues()
    matcher = _Matcher(_choice())
    turns = _Turns()
    model = _Model(["  先烧水。  "])
    opened = await submit_first_reply(
        dialogues,
        matcher,
        turns,
        model,
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question=_QUESTION,
        now=_AT,
    )
    assert opened is not None
    assert opened.reply_text == "先烧水。"
    assert opened.first_reply_at == _AT
    assert turns.history_at == _AT
    assert turns.rows == 1
    assert matcher.calls == 1
    assert dialogues.snapshot_reads == 1
    assert model.calls == 1
    assert model.seen[0]["detail"] == "先烧水，再闷三分钟。"
    assert model.seen[0]["reference_links"] == ("https://tea.example/a",)
    assert model.seen[0]["dialogue"] == ()
    assert "body" not in model.seen[0]
    again = await submit_first_reply(
        dialogues,
        matcher,
        turns,
        model,
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question="换一个问题",
        now=_LATER,
    )
    assert again is not None
    assert again.reply_text == "先烧水。"
    assert again.first_reply_at == _AT
    assert matcher.calls == 1
    assert model.calls == 1
    assert dialogues.snapshot_reads == 1
    assert turns.rows == 1


@pytest.mark.asyncio
async def test_failed_reply_stays_out_of_history_and_retries_the_answer() -> None:
    dialogues = _Dialogues()
    matcher = _Matcher(_choice())
    turns = _Turns()
    model = _Model([AnswerCallFailed(), "可以再试。"])
    missed = await submit_first_reply(
        dialogues,
        matcher,
        turns,
        model,
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question=_QUESTION,
        now=_AT,
    )
    assert missed is None
    assert dialogues.inserts == 1
    assert dialogues.saved is not None
    assert dialogues.saved.first_reply_at is None
    assert turns.history_at is None
    assert turns.row == StoredTurn(None, "failed")
    assert matcher.calls == 1
    assert model.calls == 1
    dialogues.copied = _snapshot("保存后的新描述")
    retried = await submit_first_reply(
        dialogues,
        matcher,
        turns,
        model,
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question=_QUESTION,
        now=_LATER,
    )
    assert retried is not None
    assert retried.reply_text == "可以再试。"
    assert retried.first_reply_at == _LATER
    assert matcher.calls == 1
    assert dialogues.snapshot_reads == 1
    assert model.calls == 2
    assert model.seen[1]["detail"] == "先烧水，再闷三分钟。"
    assert turns.rows == 1


@pytest.mark.asyncio
async def test_no_match_does_not_answer() -> None:
    dialogues = _Dialogues()
    model = _Model(["不会被调用"])
    assert (
        await submit_first_reply(
            dialogues,
            _Matcher(None),
            _Turns(),
            model,
            asker_user_id=_ASKER,
            client_id=_CLIENT,
            question=_QUESTION,
            now=_AT,
        )
        is None
    )
    assert model.calls == 0
    assert dialogues.inserts == 0


@pytest.mark.asyncio
async def test_blank_reply_does_not_enter_history() -> None:
    dialogues = _Dialogues()
    turns = _Turns()
    opened = await submit_first_reply(
        dialogues,
        _Matcher(_choice()),
        turns,
        _Model(["   "]),
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question=_QUESTION,
        now=_AT,
    )
    assert opened is None
    assert turns.history_at is None
    assert dialogues.inserts == 1


def test_answer_prompt_uses_the_snapshot_fields_and_not_the_skill_body() -> None:
    instruction, text = render_ask_prompt(
        detail="先烧水",
        reference_links=("https://tea.example/a",),
        question=_QUESTION,
        dialogue=(("user", "上一句"), ("spirit", "上一答")),
        text="再问一次",
    )
    assert "先烧水" in instruction
    assert "https://tea.example/a" in instruction
    assert _QUESTION in instruction
    assert "上一句" in instruction
    assert "上一答" in instruction
    assert text == "再问一次"
    rendered = inspect.getsource(render_ask_prompt)
    for forbidden in ("snapshot_body", "skill_slug", "memories", "help_count", "rank_name"):
        assert forbidden not in rendered
    answering = inspect.getsource(first_reply_module)
    for forbidden in ("craft_skills", "snapshot_body", "help_count", "craft_ledgers"):
        assert forbidden not in answering
    stored = inspect.getsource(turn_sql)
    for forbidden in ("craft_ledgers", "help_count", "craft_skills"):
        assert forbidden not in stored
    ask_call = inspect.getsource(BailianHttpAdapter.complete_ask)
    assert "_chat_instruction" not in ask_call
    assert "CHAT_PROMPT_VERSION" not in ask_call
    assert "thinking=True" not in ask_call
