"""V3-S6-T04 empty, overlong, and blocked questions do not match or answer."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.repositories.ask import SkillSnapshot, StoredAsk
from app.repositories.ask_turns import StoredTurn
from app.services.ask import submit as submit_module
from app.services.ask.first_reply import AnswerCallFailed, FirstAnswer
from app.services.ask.match import MatchChoice
from app.services.ask.submit import submit_question

_ASKER = UUID("00000000-0000-4000-8000-000000000081")
_OWNER = UUID("00000000-0000-4000-8000-000000000082")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000083")
_CLIENT = UUID("00000000-0000-4000-8000-000000000084")
_AT = datetime(2026, 11, 4, 2, 40, tzinfo=UTC)

# Product copy stays on the client. These codes are the stable server side.
_COPY = {
    "这句话不能问": "MODERATION_REJECTED",
    "这次没找成。可以再提交一次": "DEPENDENCY_UNAVAILABLE",
    "现在没有能接住这个问题的精灵": None,
}


class _Dialogues:
    def __init__(self) -> None:
        self.saved: StoredAsk | None = None
        self.inserts = 0
        self.snapshot_reads = 0

    async def find(self, asker_user_id: UUID, client_id: UUID) -> StoredAsk | None:
        del asker_user_id, client_id
        return self.saved

    async def insert(self, **fields: object) -> UUID:
        self.inserts += 1
        self.saved = StoredAsk(
            question_id=UUID("00000000-0000-4000-8000-000000000085"),
            question_text=str(fields["question_text"]),
            snapshot_title=str(fields["snapshot_title"]),
            snapshot_rank_name=None,
            snapshot_detail=str(fields["snapshot_detail"]),
            snapshot_reference_links=(),
            snapshot_body=str(fields["snapshot_body"]),
            first_reply_at=None,
        )
        return self.saved.question_id

    async def snapshot(self, skill_slug: str, spirit_id: UUID) -> SkillSnapshot | None:
        del skill_slug, spirit_id
        self.snapshot_reads += 1
        return SkillSnapshot(
            title="倒茶",
            keywords=("待客",),
            detail="先烧水。",
            reference_links=(),
            body="正文",
            points=5,
            spirit_owner_user_id=_OWNER,
        )


class _Matcher:
    def __init__(self, choice: MatchChoice | None = None) -> None:
        self.choice = choice
        self.calls = 0
        self.seen = ""

    async def match(self, question: str) -> MatchChoice | None:
        self.calls += 1
        self.seen = question
        return self.choice


class _Turns:
    async def find(
        self, question_id: UUID, asker_user_id: UUID, client_id: UUID
    ) -> StoredTurn | None:
        del question_id, asker_user_id, client_id
        return None

    async def fail(self, **fields: object) -> None:
        del fields

    async def succeed(self, **fields: object) -> datetime:
        del fields
        return _AT

    async def mark_history(
        self, question_id: UUID, asker_user_id: UUID, answered_at: datetime
    ) -> datetime:
        del question_id, asker_user_id
        return answered_at


class _Model:
    def __init__(self) -> None:
        self.calls = 0

    async def reply(self, **fields: object) -> str:
        del fields
        self.calls += 1
        return "先烧水。"


class _Moderator:
    def __init__(self, decision: str = "allow") -> None:
        self._decision = decision
        self.calls = 0

    async def decision(self, text: str) -> str:
        del text
        self.calls += 1
        return self._decision


@pytest.mark.asyncio
async def test_empty_and_overlong_questions_are_invalid_input() -> None:
    for question in ("  ", "问" * 501, f"  {'问' * 501}  "):
        dialogues = _Dialogues()
        matcher = _Matcher()
        moderator = _Moderator()
        model = _Model()
        with pytest.raises(ApiError) as caught:
            await submit_question(
                dialogues,
                matcher,
                _Turns(),
                model,
                moderator,
                asker_user_id=_ASKER,
                client_id=_CLIENT,
                question=question,
                now=_AT,
            )
        assert caught.value.code == "INVALID_INPUT"
        assert caught.value.message == "invalid input"
        assert caught.value.status_code == 422
        assert caught.value.retryable is False
        assert question not in caught.value.message
        assert moderator.calls == 0
        assert matcher.calls == 0
        assert model.calls == 0
        assert dialogues.inserts == 0


@pytest.mark.asyncio
async def test_blocked_question_is_moderation_rejected() -> None:
    dialogues = _Dialogues()
    matcher = _Matcher()
    model = _Model()
    with pytest.raises(ApiError) as caught:
        await submit_question(
            dialogues,
            matcher,
            _Turns(),
            model,
            _Moderator("block"),
            asker_user_id=_ASKER,
            client_id=_CLIENT,
            question="  不能问的事  ",
            now=_AT,
        )
    assert caught.value.code == _COPY["这句话不能问"]
    assert caught.value.message == "moderation rejected"
    assert caught.value.status_code == 422
    assert caught.value.retryable is False
    assert "不能问的事" not in caught.value.message
    assert matcher.calls == 0
    assert model.calls == 0
    assert dialogues.inserts == 0


@pytest.mark.asyncio
async def test_unreadable_check_can_be_retried_without_a_dialogue() -> None:
    dialogues = _Dialogues()
    matcher = _Matcher()
    model = _Model()
    with pytest.raises(ApiError) as caught:
        await submit_question(
            dialogues,
            matcher,
            _Turns(),
            model,
            _Moderator("error"),
            asker_user_id=_ASKER,
            client_id=_CLIENT,
            question="谁会倒茶",
            now=_AT,
        )
    assert caught.value.code == _COPY["这次没找成。可以再提交一次"]
    assert caught.value.message == "dependency unavailable"
    assert caught.value.status_code == 503
    assert caught.value.retryable is True
    assert matcher.calls == 0
    assert model.calls == 0
    assert dialogues.inserts == 0


@pytest.mark.asyncio
async def test_no_match_returns_no_dialogue_and_no_other_spirit() -> None:
    dialogues = _Dialogues()
    matcher = _Matcher(None)
    model = _Model()
    result = await submit_question(
        dialogues,
        matcher,
        _Turns(),
        model,
        _Moderator("allow"),
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question="  谁会倒茶  ",
        now=_AT,
    )
    assert _COPY["现在没有能接住这个问题的精灵"] is None
    assert result.matched is False
    assert result.question is None
    assert matcher.calls == 1
    assert matcher.seen == "谁会倒茶"
    assert model.calls == 0
    assert dialogues.inserts == 0
    assert dialogues.snapshot_reads == 0
    dumped = result.model_dump()
    assert dumped["question"] is None
    assert "spirit_name" not in dumped


@pytest.mark.asyncio
async def test_answer_failure_keeps_the_snapshot_off_the_response() -> None:
    dialogues = _Dialogues()

    class _Failing:
        def __init__(self) -> None:
            self.calls = 0

        async def reply(self, **fields: object) -> str:
            del fields
            self.calls += 1
            raise AnswerCallFailed

    model = _Failing()
    with pytest.raises(ApiError) as caught:
        await submit_question(
            dialogues,
            _Matcher(MatchChoice("pour-tea", _SPIRIT, "倒茶", ("待客",))),
            _Turns(),
            model,
            _Moderator("allow"),
            asker_user_id=_ASKER,
            client_id=_CLIENT,
            question="谁会倒茶",
            now=_AT,
        )
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert caught.value.retryable is True
    assert "谁会倒茶" not in caught.value.message
    assert dialogues.inserts == 1
    assert dialogues.saved is not None
    assert dialogues.saved.first_reply_at is None
    assert model.calls == 1


@pytest.mark.asyncio
async def test_allowed_reply_is_returned_without_a_recommended_spirit() -> None:
    result = await submit_question(
        _Dialogues(),
        _Matcher(MatchChoice("pour-tea", _SPIRIT, "倒茶", ("待客",))),
        _Turns(),
        _Model(),
        _Moderator("allow"),
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question="谁会倒茶",
        now=_AT,
    )
    assert isinstance(result, FirstAnswer)
    assert result.reply_text == "先烧水。"


def test_match_failure_does_not_recommend_another_spirit() -> None:
    source = inspect.getsource(submit_module)
    for forbidden in ("random", "spirit_name", "help_count", "craft_ledgers"):
        assert forbidden not in source
