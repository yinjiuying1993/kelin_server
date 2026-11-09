"""An unmatched question is answered by the official spirit's Bailian agent."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.logging import configure_logging
from app.domain.ask.official import (
    OFFICIAL_SLUG,
    OFFICIAL_SPIRIT_ID,
    OFFICIAL_TITLE,
    OFFICIAL_USER_ID,
)
from app.integrations.bailian import BailianHttpAdapter
from app.providers.errors import ProviderError
from app.providers.resilience import CapabilityCircuits
from app.repositories.ask import OfficialTarget, SkillSnapshot, StoredAsk
from app.repositories.ask_follow import FollowDialogue
from app.repositories.ask_rating import RateTarget, StoredRating
from app.repositories.ask_turns import StoredTurn, TurnClaim
from app.services.ask.follow import send_follow_up
from app.services.ask.match import MatchCallFailed
from app.services.ask.open import open_matched_dialogue
from app.services.ask.rate import submit_rating
from app.services.ask.submit import submit_question
from pydantic import SecretStr

_ASKER = UUID("00000000-0000-4000-8000-0000000000e1")
_CLIENT = UUID("00000000-0000-4000-8000-0000000000e2")
_QUESTION_ID = UUID("00000000-0000-4000-8000-0000000000e3")
_AT = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
_APP = "app-test-official"
_SECRET = "test-only-bailian-key"
_QUESTION = "谁能回答这个"


class _Dialogues:
    def __init__(self, target: OfficialTarget | None) -> None:
        self.target = target
        self.saved: StoredAsk | None = None
        self.fields: dict[str, object] | None = None
        self.inserts = 0

    async def find(self, asker_user_id: UUID, client_id: UUID) -> StoredAsk | None:
        del asker_user_id, client_id
        return self.saved

    async def insert(self, **fields: object) -> UUID:
        self.inserts += 1
        self.fields = fields
        self.saved = StoredAsk(
            question_id=_QUESTION_ID,
            question_text=str(fields["question_text"]),
            snapshot_title=str(fields["snapshot_title"]),
            snapshot_rank_name=None,
            snapshot_detail=str(fields["snapshot_detail"]),
            snapshot_reference_links=(),
            snapshot_body=str(fields["snapshot_body"]),
            first_reply_at=None,
            official=fields.get("snapshot_skill_slug") == OFFICIAL_SLUG,
        )
        return _QUESTION_ID

    async def snapshot(self, skill_slug: str, spirit_id: UUID) -> SkillSnapshot | None:
        del skill_slug, spirit_id
        return None

    async def official(self) -> OfficialTarget | None:
        return self.target


class _Matcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def match(self, question: str) -> None:
        del question
        self.calls += 1
        if self.fail:
            raise MatchCallFailed
        return None


class _Turns:
    def __init__(self) -> None:
        self.row: StoredTurn | None = None
        self.sessions: list[str] = []

    async def find(
        self, question_id: UUID, asker_user_id: UUID, client_id: UUID
    ) -> StoredTurn | None:
        del question_id, asker_user_id, client_id
        return self.row

    async def fail(self, **fields: object) -> None:
        del fields
        self.row = StoredTurn(None, "failed")

    async def succeed(self, **fields: object) -> datetime:
        del fields
        self.row = StoredTurn("ok", "succeeded")
        return _AT

    async def mark_history(
        self, question_id: UUID, asker_user_id: UUID, answered_at: datetime
    ) -> datetime:
        del question_id, asker_user_id
        return answered_at

    async def save_session(self, question_id: UUID, asker_user_id: UUID, session_id: str) -> None:
        del question_id, asker_user_id
        self.sessions.append(session_id)

    async def occupy(self, **fields: object) -> TurnClaim:
        return TurnClaim("owned", user_text=str(fields["user_text"]))


class _SkillModel:
    def __init__(self) -> None:
        self.calls = 0

    async def reply(self, **fields: object) -> str:
        del fields
        self.calls += 1
        return "技能回复"


class _OfficialModel:
    def __init__(self, session: str | None = "sess-1") -> None:
        self.session = session
        self.seen: list[tuple[str, str | None]] = []

    async def reply(self, *, text: str, session_id: str | None) -> tuple[str, str | None]:
        self.seen.append((text, session_id))
        return "官方答复", self.session


class _Moderator:
    async def decision(self, text: str) -> str:
        del text
        return "allow"


def _target() -> OfficialTarget:
    return OfficialTarget(OFFICIAL_SPIRIT_ID, OFFICIAL_USER_ID)


@pytest.mark.asyncio
async def test_no_match_asks_the_official_spirit_not_the_skill_model() -> None:
    dialogues = _Dialogues(_target())
    turns = _Turns()
    skill = _SkillModel()
    official = _OfficialModel()
    opened = await open_matched_dialogue(
        dialogues, _Matcher(), asker_user_id=_ASKER, client_id=_CLIENT, question=_QUESTION
    )
    assert opened is not None and opened.official is True
    assert dialogues.fields is not None
    assert dialogues.fields["spirit_id"] == OFFICIAL_SPIRIT_ID
    assert dialogues.fields["snapshot_title"] == OFFICIAL_TITLE
    assert dialogues.fields["snapshot_rank_name"] is None
    assert dialogues.fields["snapshot_skill_slug"] == OFFICIAL_SLUG
    result = await submit_question(
        dialogues,
        _Matcher(),
        turns,
        skill,
        _Moderator(),
        asker_user_id=_ASKER,
        client_id=_CLIENT,
        question=_QUESTION,
        now=_AT,
        official_model=official,
    )
    assert result.reply_text == "官方答复"
    assert skill.calls == 0
    assert official.seen == [(_QUESTION, None)]
    assert turns.sessions == ["sess-1"]
    assert "official-general" not in result.reply_text


@pytest.mark.asyncio
async def test_match_model_failure_does_not_open_the_official_spirit() -> None:
    dialogues = _Dialogues(_target())
    with pytest.raises(ApiError) as caught:
        await submit_question(
            dialogues,
            _Matcher(fail=True),
            _Turns(),
            _SkillModel(),
            _Moderator(),
            asker_user_id=_ASKER,
            client_id=_CLIENT,
            question=_QUESTION,
            now=_AT,
            official_model=_OfficialModel(),
        )
    assert caught.value.code == "DEPENDENCY_UNAVAILABLE"
    assert caught.value.status_code == 503
    assert caught.value.retryable is True
    assert _QUESTION not in caught.value.message
    assert dialogues.inserts == 0


@pytest.mark.asyncio
async def test_official_follow_up_sends_the_saved_session() -> None:
    official = _OfficialModel("sess-2")
    skill = _SkillModel()
    turns = _Turns()
    reply = await send_follow_up(
        _Follow(),
        turns,
        skill,
        _Moderator(),
        asker_user_id=_ASKER,
        question_id=_QUESTION_ID,
        client_id=_CLIENT,
        text="接着说",
        now=_AT,
        official_model=official,
    )
    assert reply.reply_text == "官方答复"
    assert skill.calls == 0
    assert official.seen == [("接着说", "sess-1")]
    assert turns.sessions == ["sess-2"]


class _Follow:
    async def load(self, asker_user_id: UUID, question_id: UUID) -> FollowDialogue:
        del asker_user_id, question_id
        return FollowDialogue(
            question_id=_QUESTION_ID,
            question_text=_QUESTION,
            snapshot_detail=OFFICIAL_TITLE,
            snapshot_reference_links=(),
            first_reply_at=_AT,
            rated=False,
            turns=(("user", _QUESTION), ("spirit", "官方答复")),
            official=True,
            agent_session_id="sess-1",
        )


class _Ratings:
    def __init__(self) -> None:
        self.saved: StoredRating | None = None
        self.jobs: list[object] = []

    async def target(self, asker_user_id: UUID, question_id: UUID) -> RateTarget:
        del asker_user_id, question_id
        return RateTarget(OFFICIAL_SPIRIT_ID, OFFICIAL_USER_ID, OFFICIAL_SLUG, True)

    async def find(self, asker_user_id: UUID, question_id: UUID) -> StoredRating | None:
        del asker_user_id, question_id
        return self.saved

    async def insert(
        self, *, asker_user_id: UUID, question_id: UUID, rating: str, points: int
    ) -> bool:
        del asker_user_id, question_id
        self.saved = StoredRating(rating, points)
        return True

    async def enqueue(self, job: object) -> None:
        self.jobs.append(job)


@pytest.mark.asyncio
async def test_official_rating_is_stored_without_a_settlement() -> None:
    store = _Ratings()
    result = await submit_rating(
        store,
        asker_user_id=_ASKER,
        question_id=_QUESTION_ID,
        client_id=_CLIENT,
        rating="非常满意",
    )
    assert result.rating == "非常满意"
    assert store.saved is not None
    assert store.saved.points == 10
    assert store.jobs == []


@pytest.mark.asyncio
async def test_official_agent_call_stays_out_of_the_log() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"output": {"text": "  智能体答复  ", "session_id": " sess-9 "}},
        )

    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = BailianHttpAdapter.from_settings(
                Settings(
                    app_env="test",
                    bailian_api_key=SecretStr(_SECRET),
                    bailian_chat_model="chat",
                    bailian_official_app_id=_APP,
                ),
                client=client,
                circuits=CapabilityCircuits(),
                sleep=_no_sleep,
                rng=random.Random(0),
            )
            reply, session = await adapter.complete_official(_QUESTION, "sess-1")
    assert reply == "智能体答复"
    assert session == "sess-9"
    assert len(seen) == 1
    assert str(seen[0].url) == f"https://dashscope.aliyuncs.com/api/v1/apps/{_APP}/completion"
    body = json.loads(seen[0].content)
    assert body["input"] == {"prompt": _QUESTION, "session_id": "sess-1"}
    assert "messages" not in body
    dumped = buf.getvalue()
    assert _QUESTION not in dumped
    assert _APP not in dumped
    assert _SECRET not in dumped
    assert "智能体答复" not in dumped


@pytest.mark.asyncio
async def test_missing_official_app_does_not_call() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = BailianHttpAdapter.from_settings(
            Settings(
                app_env="test",
                bailian_api_key=SecretStr(_SECRET),
                bailian_chat_model="chat",
                bailian_official_app_id=None,
            ),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        with pytest.raises(ProviderError):
            await adapter.complete_official(_QUESTION, None)


async def _no_sleep(_delay: float) -> None:
    return None
