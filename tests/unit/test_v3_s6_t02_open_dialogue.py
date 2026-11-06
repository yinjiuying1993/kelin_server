"""V3-S6-T02 one idempotency key opens one dialogue and matches once."""

from __future__ import annotations

import inspect
from uuid import UUID

import pytest
from app.repositories import ask as ask_sql
from app.repositories.ask import SkillSnapshot, StoredAsk
from app.services.ask.match import MatchChoice
from app.services.ask.open import open_matched_dialogue

_ASKER = UUID("00000000-0000-4000-8000-000000000061")
_OWNER = UUID("00000000-0000-4000-8000-000000000062")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000063")
_CLIENT = UUID("00000000-0000-4000-8000-000000000064")
_QUESTION = "谁会倒茶"


def _choice() -> MatchChoice:
    return MatchChoice(
        skill_slug="pour-tea",
        spirit_id=_SPIRIT,
        title="倒茶",
        keywords=("待客",),
    )


def _snapshot(body: str = "进入时的正文") -> SkillSnapshot:
    return SkillSnapshot(
        title="倒茶",
        keywords=("待客",),
        detail="先烧水，再闷三分钟。",
        reference_links=("https://tea.example/a",),
        body=body,
        points=5,
        spirit_owner_user_id=_OWNER,
    )


class _Dialogues:
    def __init__(self, copied: SkillSnapshot | None) -> None:
        self.copied = copied
        self.saved: StoredAsk | None = None
        self.snapshot_reads = 0
        self.inserts = 0

    async def find(self, asker_user_id: UUID, client_id: UUID) -> StoredAsk | None:
        del asker_user_id, client_id
        return self.saved

    async def insert(self, **fields: object) -> UUID:
        self.inserts += 1
        self.saved = StoredAsk(
            question_id=UUID("00000000-0000-4000-8000-000000000065"),
            question_text=str(fields["question_text"]),
            snapshot_title=str(fields["snapshot_title"]),
            snapshot_rank_name=fields["snapshot_rank_name"],  # type: ignore[arg-type]
            snapshot_detail=str(fields["snapshot_detail"]),
            snapshot_reference_links=fields["snapshot_reference_links"],  # type: ignore[arg-type]
            snapshot_body=str(fields["snapshot_body"]),
            first_reply_at=None,
        )
        return self.saved.question_id

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


@pytest.mark.asyncio
async def test_same_key_matches_once_and_keeps_the_snapshot() -> None:
    dialogues = _Dialogues(_snapshot())
    matcher = _Matcher(_choice())
    opened = await open_matched_dialogue(
        dialogues, matcher, asker_user_id=_ASKER, client_id=_CLIENT, question=_QUESTION
    )
    assert opened is not None
    assert opened.first_reply_at is None
    assert opened.snapshot_body == "进入时的正文"
    assert opened.snapshot_rank_name == "门边学徒"
    assert opened.snapshot_detail == "先烧水，再闷三分钟。"
    assert matcher.calls == 1
    assert dialogues.snapshot_reads == 1
    assert dialogues.inserts == 1
    dialogues.copied = _snapshot("保存后的新正文")
    again = await open_matched_dialogue(
        dialogues,
        matcher,
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question="换一个问题",
    )
    assert again is not None
    assert again.question_id == opened.question_id
    assert again.snapshot_body == "进入时的正文"
    assert again.question_text == _QUESTION
    assert matcher.calls == 1
    assert dialogues.snapshot_reads == 1
    assert dialogues.inserts == 1


@pytest.mark.asyncio
async def test_failed_match_or_changed_skill_writes_no_dialogue() -> None:
    missed = _Dialogues(_snapshot())
    matcher = _Matcher(None)
    assert (
        await open_matched_dialogue(
            missed, matcher, asker_user_id=_ASKER, client_id=_CLIENT, question=_QUESTION
        )
        is None
    )
    assert matcher.calls == 1
    assert missed.inserts == 0
    assert missed.snapshot_reads == 0
    changed = _Dialogues(_snapshot())
    changed.copied = SkillSnapshot(
        title="新倒茶",
        keywords=("新词",),
        detail="先烧水，再闷三分钟。",
        reference_links=(),
        body="新正文",
        points=5,
        spirit_owner_user_id=_OWNER,
    )
    matcher = _Matcher(_choice())
    assert (
        await open_matched_dialogue(
            changed,
            matcher,
            asker_user_id=_ASKER,
            client_id=_CLIENT,
            question=_QUESTION,
        )
        is None
    )
    assert changed.inserts == 0


def test_opening_a_dialogue_does_not_write_history_or_help_counts() -> None:
    source = inspect.getsource(ask_sql)
    for forbidden in ("ask_turns", "help_count", "craft_ledgers", "first_reply_at ="):
        assert forbidden not in source
    opened = inspect.getsource(open_matched_dialogue)
    assert "first_reply_at=None" in opened
    assert opened.count("snapshot(") == 1
