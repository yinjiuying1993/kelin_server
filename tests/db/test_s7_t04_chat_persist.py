"""S7-T04: model runs outside the transaction; dialogue.completed follows the pair."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

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
                    "document_version": "2026-09",
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": "2026-09", "displayed": True},
                "user_terms": {"document_version": "2026-09", "displayed": True},
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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _counts(url: str, spirit_id: uuid.UUID, owner_id: uuid.UUID) -> tuple[int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        messages = await conn.scalar(
            text("SELECT count(*) FROM public.messages WHERE spirit_id = :spirit_id"),
            {"spirit_id": spirit_id},
        )
        tasks = await conn.scalar(
            text(
                "SELECT count(*) FROM public.outbox_events "
                "WHERE owner_id = :owner_id AND event_type = 'extract.requested'"
            ),
            {"owner_id": owner_id},
        )
    await engine.dispose()
    return int(messages or 0), int(tasks or 0)


class _RecordingProvider:
    source = "stub"

    def __init__(self, url: str, spirit_id: uuid.UUID, owner_id: uuid.UUID) -> None:
        self.url = url
        self.spirit_id = spirit_id
        self.owner_id = owner_id
        self.during: tuple[int, int] | None = None

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        if self.during is None:
            self.during = await _counts(self.url, self.spirit_id, self.owner_id)
        return ChatOutput(reply="我在。")

    async def search(self, value: object) -> list[object]:
        del value
        return []


class _VersionBump:
    source = "stub"

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        user: CurrentUser,
        spirit_id: uuid.UUID,
    ) -> None:
        self.factory = factory
        self.user = user
        self.spirit_id = spirit_id

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        async with claimed_transaction(self.factory, self.user) as session:
            await session.execute(
                text("UPDATE public.spirits SET version = version + 1 WHERE id = :id"),
                {"id": self.spirit_id},
            )
        return ChatOutput(reply="我在。")

    async def search(self, value: object) -> list[object]:
        del value
        return []


class _FailingProvider:
    source = "stub"

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def search(self, value: object) -> list[object]:
        del value
        return []


class _LateMemory:
    source = "stub"

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        user: CurrentUser,
        spirit_id: uuid.UUID,
    ) -> None:
        self.factory = factory
        self.user = user
        self.spirit_id = spirit_id
        self.planted = uuid.uuid4()
        self.seen: list[uuid.UUID] = []

    async def chat(self, value: ChatInput) -> ChatOutput:
        self.seen = [item.id for item in value.memories]
        async with claimed_transaction(self.factory, self.user) as session:
            await session.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence"
                    ") VALUES (:id, :spirit_id, 'preference', '新记住的', 80, 0.900)"
                ),
                {"id": self.planted, "spirit_id": self.spirit_id},
            )
        return ChatOutput(reply="我在。")

    async def search(self, value: object) -> list[object]:
        del value
        return []


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


def test_three_rounds_publish_one_extract_after_persist() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_persist_then_publish(url))


async def _assert_persist_then_publish(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    provider = _RecordingProvider(url, spirit_id, user.id)
    third = _chat_request("第三句")
    for content in ("第一句", "第二句"):
        await complete_chat_turn(factory, user, _chat_request(content), now=NOW, provider=provider)
    await complete_chat_turn(factory, user, third, now=NOW, provider=provider)
    assert provider.during == (0, 0)
    assert await _counts(url, spirit_id, user.id) == (6, 1)
    await complete_chat_turn(factory, user, third, now=NOW, provider=provider)
    await complete_chat_turn(factory, user, _chat_request("第四句"), now=NOW, provider=provider)
    assert await _counts(url, spirit_id, user.id) == (8, 1)
    await engine.dispose()


def test_version_change_during_model_call_does_not_persist() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_version_conflict(url))


async def _assert_version_conflict(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    provider = _VersionBump(factory, user, spirit_id)
    try:
        await complete_chat_turn(
            factory, user, _chat_request("你好"), now=NOW, provider=provider
        )
    except ApiError as exc:
        assert exc.code == "CONFLICT"
        assert exc.status_code == 409
    else:
        raise AssertionError("expected CONFLICT")
    assert await _counts(url, spirit_id, user.id) == (0, 0)
    await engine.dispose()


def test_model_failure_writes_no_reply() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_model_failure(url))


async def _assert_model_failure(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    try:
        await complete_chat_turn(
            factory, user, _chat_request("你好"), now=NOW, provider=_FailingProvider()
        )
    except ApiError as exc:
        assert exc.code == "MODEL_UNAVAILABLE"
        assert exc.status_code == 503
    else:
        raise AssertionError("expected MODEL_UNAVAILABLE")
    assert await _counts(url, spirit_id, user.id) == (0, 0)
    await engine.dispose()


class _NamingProvider:
    source = "stub"

    def __init__(self, output: ChatOutput) -> None:
        self.output = output

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        return self.output

    async def search(self, value: object) -> list[object]:
        del value
        return []


async def _spirit_name(url: str, spirit_id: uuid.UUID) -> str:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        name = await conn.scalar(
            text("SELECT name FROM public.spirits WHERE id = :spirit_id"),
            {"spirit_id": spirit_id},
        )
    await engine.dispose()
    return str(name)


def test_model_naming_flag_writes_the_spirit_name() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_model_names_the_spirit(url))


async def _assert_model_names_the_spirit(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    named = await complete_chat_turn(
        factory,
        user,
        _chat_request("好。以后你就叫 依依"),
        now=NOW,
        provider=_NamingProvider(
            ChatOutput(
                reply="好，那我以后就叫依依。",
                intent="companion",
                naming=True,
                given_name="依依",
            )
        ),
    )
    assert named.spirit.name == "依依"
    assert await _spirit_name(url, spirit_id) == "依依"
    await complete_chat_turn(
        factory,
        user,
        _chat_request("你叫什么"),
        now=NOW,
        provider=_NamingProvider(
            ChatOutput(reply="我叫依依。", intent="companion", naming=False, given_name="依依")
        ),
    )
    assert await _spirit_name(url, spirit_id) == "依依"
    await engine.dispose()


def test_turn_does_not_use_memory_inserted_during_generation() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_snapshot_memories(url))


async def _assert_snapshot_memories(url: str) -> None:
    user, engine, factory, spirit_id = await _owner(url)
    provider = _LateMemory(factory, user, spirit_id)
    await complete_chat_turn(factory, user, _chat_request("你好"), now=NOW, provider=provider)
    assert provider.planted not in provider.seen
    assert await _counts(url, spirit_id, user.id) == (2, 0)
    await engine.dispose()
