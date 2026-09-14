from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.chat import UNKNOWN_SOURCE_REPLY
from app.providers.errors import ProviderError
from app.providers.stub import ControllableProviderStub
from app.providers.types import ChatInput, ChatOutput, SearchInput, SearchResult
from app.schemas.chat import ChatRequest
from app.schemas.onboarding import CompleteOnboardingRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import settle_chat_turn
from app.services.onboarding import complete_onboarding
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
QUESTION = "今天外面发生了什么"
FABRICATED = "根据网上消息，结论是这样。"


class _SearchProvider(ControllableProviderStub):
    def __init__(
        self,
        *,
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self._results = results or []
        self._error = error
        self.search_calls: list[SearchInput] = []

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        return ChatOutput(reply=FABRICATED, search_query=QUESTION)

    async def search(self, value: SearchInput) -> list[SearchResult]:
        self.search_calls.append(value)
        if self._error is not None:
            raise self._error
        return list(self._results)


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


def _chat_request() -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(uuid.uuid4()),
            "content": QUESTION,
            "source": "text",
            "onboarding": False,
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 21,
                "weather": "cloudy",
                "city": None,
            },
        }
    )


def _complete_request(*, version: int) -> CompleteOnboardingRequest:
    return CompleteOnboardingRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "expected_spirit_version": version,
        }
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _hatch(session: AsyncSession, user: CurrentUser) -> uuid.UUID:
    created = await create_spirit_if_absent(session, user, _spirit_request())
    version = created.version
    for _ in range(5):
        turned = await settle_chat_turn(
            session,
            user,
            ChatRequest.model_validate(
                {
                    "client_message_id": str(uuid.uuid4()),
                    "content": "你好",
                    "source": "text",
                    "onboarding": True,
                    "context": {
                        "timezone": "Asia/Shanghai",
                        "local_hour": 21,
                        "weather": "cloudy",
                        "city": None,
                    },
                }
            ),
            now=NOW,
        )
        version = turned.version
    hatched = await complete_onboarding(session, user, _complete_request(version=version), now=NOW)
    assert hatched.spirit.hatched_at is not None
    return created.spirit_id


def test_remote_search_off_and_untrusted_sources_admit_unknown() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_search_safety(url))


async def _assert_search_safety(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        spirit_id = await _hatch(session, user)

    disabled = _SearchProvider(results=[SearchResult(url="https://trusted.example/a")])
    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text("UPDATE public.user_preferences SET remote_search_on = false WHERE user_id = :id"),
            {"id": user_id},
        )
        settled = await settle_chat_turn(session, user, _chat_request(), now=NOW, provider=disabled)
    assert settled.spirit_content == UNKNOWN_SOURCE_REPLY
    assert settled.spirit_source_refs == ()
    assert disabled.search_calls == []
    async with claimed_transaction(factory, user) as session:
        stored = await session.scalar(
            text("SELECT content FROM public.messages WHERE id = :id"),
            {"id": settled.spirit_message_id},
        )
        refs = await session.scalar(
            text("SELECT source_refs FROM public.messages WHERE id = :id"),
            {"id": settled.spirit_message_id},
        )
    assert stored == UNKNOWN_SOURCE_REPLY
    assert refs == []

    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text("UPDATE public.user_preferences SET remote_search_on = true WHERE user_id = :id"),
            {"id": user_id},
        )

    failed = _SearchProvider(error=ProviderError("MODEL_UNAVAILABLE"))
    async with claimed_transaction(factory, user) as session:
        settled = await settle_chat_turn(session, user, _chat_request(), now=NOW, provider=failed)
    assert settled.spirit_content == UNKNOWN_SOURCE_REPLY
    assert settled.spirit_source_refs == ()
    assert len(failed.search_calls) == 1

    untrusted = _SearchProvider(
        results=[
            SearchResult(url="http://insecure.example/a"),
            SearchResult(url="https://localhost/x"),
        ]
    )
    async with claimed_transaction(factory, user) as session:
        settled = await settle_chat_turn(
            session, user, _chat_request(), now=NOW, provider=untrusted
        )
    assert settled.spirit_content == UNKNOWN_SOURCE_REPLY
    assert settled.spirit_source_refs == ()

    trusted = _SearchProvider(
        results=[
            SearchResult(url="https://one.example/a"),
            SearchResult(url="https://one.example/b"),
            SearchResult(url="https://two.example/c"),
        ]
    )
    async with claimed_transaction(factory, user) as session:
        settled = await settle_chat_turn(session, user, _chat_request(), now=NOW, provider=trusted)
        stored_refs = await session.scalar(
            text("SELECT source_refs FROM public.messages WHERE id = :id"),
            {"id": settled.spirit_message_id},
        )
    assert settled.spirit_content == FABRICATED
    assert [ref.url for ref in settled.spirit_source_refs] == [
        "https://one.example/a",
        "https://two.example/c",
    ]
    assert all(ref.type == "web" for ref in settled.spirit_source_refs)
    assert str(stored_refs[0]["url"]) == "https://one.example/a"
    del spirit_id
    await engine.dispose()
