"""S7-T05: lost and away never enter companion chat; model failure saves nothing."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.errors import ProviderError
from app.providers.types import ChatInput, ChatOutput
from app.schemas.chat import ChatRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import complete_chat_turn
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {
                    "document_version": "2026-11-12",
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": "2026-11-12", "displayed": True},
                "user_terms": {"document_version": "2026-11-12", "displayed": True},
            },
        }
    )


def _chat_request(content: str) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(uuid.uuid4()),
            "content": content,
            "source": "text",
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 21,
                "weather": "cloudy",
                "city": None,
            },
        }
    )


class _Companion:
    source = "stub"

    def __init__(self) -> None:
        self.called = False

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        self.called = True
        return ChatOutput(reply="我正陪着你。天气很好。")

    async def search(self, value: object) -> list[object]:
        del value
        return []


class _Failing:
    source = "stub"

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        self.calls += 1
        raise ProviderError("MODEL_UNAVAILABLE")

    async def search(self, value: object) -> list[object]:
        del value
        return []


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _owner(
    url: str,
) -> tuple[CurrentUser, object, async_sessionmaker[AsyncSession], uuid.UUID]:
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
    return user, engine, factory, created.spirit_id


async def _place(url: str, spirit_id: uuid.UUID, status: str) -> None:
    until = NOW + timedelta(hours=2)
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        params = {
            "id": spirit_id,
            "status": status,
            "now": NOW,
            "study": until if status == "study" else None,
            "away": until if status == "away" else None,
            "lost": status == "lost",
        }
        await conn.execute(
            text(
                "UPDATE public.spirits SET status = :status, hatched_at = :now, "
                "last_interact_at = :now, study_until = :study, away_until = :away, "
                "has_been_lost = :lost WHERE id = :id"
            ),
            params,
        )
        await conn.execute(
            text(
                "UPDATE public.spirit_awareness SET status = :status, "
                "last_interact_at = :now, study_until = :study, away_until = :away, "
                "has_been_lost = :lost, last_state_settled_at = :now "
                "WHERE spirit_id = :id"
            ),
            params,
        )
    await engine.dispose()


async def _stored(url: str, spirit_id: uuid.UUID) -> tuple[str | None, int, int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        reply = await conn.scalar(
            text(
                "SELECT content FROM public.messages "
                "WHERE spirit_id = :id AND role = 'spirit'"
            ),
            {"id": spirit_id},
        )
        rounds = await conn.scalar(
            text("SELECT ordinary_dialogue_rounds FROM public.spirits WHERE id = :id"),
            {"id": spirit_id},
        )
        tasks = await conn.scalar(
            text(
                "SELECT count(*) FROM public.outbox_events e "
                "JOIN public.conversation_windows w ON w.id = e.aggregate_id "
                "WHERE w.spirit_id = :id AND e.event_type = 'extract.requested'"
            ),
            {"id": spirit_id},
        )
        messages = await conn.scalar(
            text("SELECT count(*) FROM public.messages WHERE spirit_id = :id"),
            {"id": spirit_id},
        )
    await engine.dispose()
    text_reply = None if reply is None else str(reply)
    return text_reply, int(rounds or 0), int(tasks or 0), int(messages or 0)


def test_lost_knowledge_question_does_not_enter_companion() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_lost(url))


async def _assert_lost(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    await _place(url, spirit_id, "lost")
    provider = _Companion()
    try:
        await complete_chat_turn(
            factory, user, _chat_request("天气怎么样"), now=NOW, provider=provider
        )
    except ApiError as exc:
        assert exc.code == "SPIRIT_NOT_HOME"
        assert exc.status_code == 409
    else:
        raise AssertionError("expected SPIRIT_NOT_HOME")
    reply, rounds, tasks, messages = await _stored(url, spirit_id)
    assert provider.called is False
    assert reply is None
    assert rounds == 0
    assert tasks == 0
    assert messages == 0
    await engine.dispose()


def test_lost_safety_stays_fixed_and_skips_companion() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_lost_safety(url))


async def _assert_lost_safety(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    await _place(url, spirit_id, "lost")
    provider = _Companion()
    try:
        await complete_chat_turn(
            factory, user, _chat_request("我不想活了"), now=NOW, provider=provider
        )
    except ApiError as exc:
        assert exc.code == "SPIRIT_NOT_HOME"
    else:
        raise AssertionError("expected SPIRIT_NOT_HOME")
    reply, rounds, tasks, messages = await _stored(url, spirit_id)
    assert provider.called is False
    assert reply is None
    assert rounds == 0
    assert tasks == 0
    assert messages == 0
    await engine.dispose()


def test_away_and_study_do_not_pretend_to_be_present() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_away(url))


async def _assert_away(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    await _place(url, spirit_id, "study")
    provider = _Companion()
    try:
        await complete_chat_turn(factory, user, _chat_request("在吗"), now=NOW, provider=provider)
    except ApiError as exc:
        assert exc.code == "SPIRIT_NOT_HOME"
    else:
        raise AssertionError("expected SPIRIT_NOT_HOME")
    reply, rounds, tasks, messages = await _stored(url, spirit_id)
    assert provider.called is False
    assert reply is None
    assert rounds == 0
    assert tasks == 0
    assert messages == 0

    away_user, away_engine, away_factory, away_id = await _owner(url)
    await _place(url, away_id, "away")
    away_provider = _Companion()
    try:
        await complete_chat_turn(
            away_factory, away_user, _chat_request("陪我聊聊"), now=NOW, provider=away_provider
        )
    except ApiError as exc:
        assert exc.code == "SPIRIT_NOT_HOME"
    else:
        raise AssertionError("expected SPIRIT_NOT_HOME")
    away_reply, away_rounds, away_tasks, away_messages = await _stored(url, away_id)
    assert away_provider.called is False
    assert away_reply is None
    assert away_rounds == 0
    assert away_tasks == 0
    assert away_messages == 0
    await engine.dispose()
    await away_engine.dispose()


class _Scripted:
    source = "stub"

    def __init__(self, output: ChatOutput) -> None:
        self._output = output

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        return self._output

    async def search(self, value: object) -> list[object]:
        del value
        return []


def test_rejected_model_text_is_replaced_by_fallback() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_fallback(url))


async def _assert_fallback(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    long_reply = "第一句。第二句。第三句。第四句。"
    settled = await complete_chat_turn(
        factory,
        user,
        _chat_request("在吗"),
        now=NOW,
        provider=_Scripted(ChatOutput(reply=long_reply, intent="companion")),
    )
    assert settled.spirit_content == long_reply

    mismatch = await complete_chat_turn(
        factory,
        user,
        _chat_request("还在"),
        now=NOW,
        provider=_Scripted(ChatOutput(reply="饿了也陪你。", intent="hungry")),
    )
    assert mismatch.spirit_content == "饿了也陪你。"
    reply, _rounds, _tasks, messages = await _stored(url, spirit_id)
    assert messages == 4
    assert reply == "饿了也陪你。"
    await engine.dispose()


def test_knowledge_without_evidence_rejects_taught_claim() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_taught_claim(url))


async def _assert_taught_claim(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    taught = "你教过我。情境是第一步。"
    provider = _Scripted(ChatOutput(reply=taught, intent="knowledge"))
    settled = await complete_chat_turn(
        factory, user, _chat_request("STAR 是什么"), now=NOW, provider=provider
    )
    assert settled.spirit_content == taught
    _reply, _rounds, _tasks, messages = await _stored(url, spirit_id)
    assert messages == 2
    await engine.dispose()


def test_forbidden_address_blocks_the_next_reply() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_overlay(url))


async def _assert_overlay(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    opened = await complete_chat_turn(
        factory,
        user,
        _chat_request("别再叫宝宝"),
        now=NOW,
        provider=_Scripted(ChatOutput(reply="好。", intent="companion")),
    )
    assert opened.spirit_content == "好。"
    check = create_async_engine(url)
    async with check.connect() as conn:
        payload = await conn.scalar(
            text("SELECT payload::text FROM public.chat_overlays WHERE spirit_id = :id"),
            {"id": spirit_id},
        )
    await check.dispose()
    assert payload is not None
    assert "宝宝" in payload
    nxt = await complete_chat_turn(
        factory,
        user,
        _chat_request("在吗"),
        now=NOW + timedelta(seconds=1),
        provider=_Scripted(ChatOutput(reply="宝宝在吗。", intent="companion")),
    )
    assert nxt.spirit_content == "宝宝在吗。"
    await engine.dispose()


def test_provider_failure_writes_no_reply() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_failure(url))


async def _assert_failure(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    provider = _Failing()
    try:
        await complete_chat_turn(
            factory, user, _chat_request("你好"), now=NOW, provider=provider
        )
    except ApiError as exc:
        assert exc.code == "MODEL_UNAVAILABLE"
    else:
        raise AssertionError("expected MODEL_UNAVAILABLE")
    reply, _rounds, tasks, messages = await _stored(url, spirit_id)
    assert provider.calls == 1
    assert reply is None
    assert messages == 0
    assert tasks == 0
    await engine.dispose()


class _Sequence:
    source = "stub"

    def __init__(self, outputs: list[ChatOutput | ProviderError]) -> None:
        self._outputs = list(outputs)
        self.calls: list[ChatInput] = []

    async def chat(self, value: ChatInput) -> ChatOutput:
        self.calls.append(value)
        item = self._outputs.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return item

    async def search(self, value: object) -> list[object]:
        del value
        return []


def test_invalid_reply_retries_once() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_retry(url))


async def _assert_retry(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    long_reply = "第一句。第二句。第三句。第四句。"
    recovered = _Sequence([ChatOutput(reply=long_reply, intent="knowledge")])
    settled = await complete_chat_turn(
        factory, user, _chat_request("在吗"), now=NOW, provider=recovered
    )
    assert len(recovered.calls) == 1
    assert settled.spirit_content == long_reply

    kept = _Sequence([ChatOutput(reply=long_reply, intent="companion")])
    accepted = await complete_chat_turn(
        factory, user, _chat_request("还在"), now=NOW, provider=kept
    )
    assert len(kept.calls) == 1
    assert accepted.spirit_content == long_reply

    parsed = _Sequence(
        [
            ProviderError("CHAT_OUTPUT_INVALID"),
            ChatOutput(reply="好。", intent="companion"),
        ]
    )
    repaired = await complete_chat_turn(
        factory, user, _chat_request("你好"), now=NOW, provider=parsed
    )
    assert len(parsed.calls) == 2
    assert "rejection=structure" in parsed.calls[1].emotion_context
    assert repaired.spirit_content == "好。"
    reply, _rounds, _tasks, messages = await _stored(url, spirit_id)
    assert messages == 6
    assert reply == "好。"
    await engine.dispose()
