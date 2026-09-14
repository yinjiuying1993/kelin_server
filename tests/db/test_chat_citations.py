from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.stub import ControllableProviderStub
from app.providers.types import ChatCitation, ChatInput, ChatOutput
from app.schemas.chat import ChatRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import settle_chat_turn
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
FAKE_REPLY = "知道了，阿年。"


class _CitedProvider(ControllableProviderStub):
    def __init__(self, output: ChatOutput) -> None:
        super().__init__()
        self._output = output

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        return self._output


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
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _message_count(session: AsyncSession, spirit_id: uuid.UUID) -> int:
    count = await session.scalar(
        text("SELECT count(*) FROM public.messages WHERE spirit_id = :id"),
        {"id": spirit_id},
    )
    return int(count or 0)


def test_unauthorized_and_inactive_citations_do_not_persist_reply() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_citation_owner_gate(url))


async def _assert_citation_owner_gate(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        spirit_id = created.spirit_id

    foreign = uuid.uuid4()
    with pytest.raises(ApiError) as unauthorized:
        async with claimed_transaction(factory, user) as session:
            await settle_chat_turn(
                session,
                user,
                _chat_request(),
                now=NOW,
                provider=_CitedProvider(
                    ChatOutput(
                        reply=FAKE_REPLY,
                        citations=[ChatCitation(type="memory", id=foreign)],
                    )
                ),
            )
    assert unauthorized.value.code == "MODEL_UNAVAILABLE"
    async with claimed_transaction(factory, user) as session:
        assert await _message_count(session, spirit_id) == 0
        stored = await session.scalar(
            text("SELECT count(*) FROM public.messages WHERE content = :content"),
            {"content": FAKE_REPLY},
        )
        assert int(stored or 0) == 0

    sealed_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, status, sealed_at"
                ") VALUES ("
                ":id, :spirit_id, 'preference', '称呼', 90, 0.96, 'sealed', :now"
                ")"
            ),
            {"id": sealed_id, "spirit_id": spirit_id, "now": NOW},
        )
    with pytest.raises(ApiError) as inactive:
        async with claimed_transaction(factory, user) as session:
            await settle_chat_turn(
                session,
                user,
                _chat_request(),
                now=NOW,
                provider=_CitedProvider(
                    ChatOutput(
                        reply=FAKE_REPLY,
                        citations=[ChatCitation(type="memory", id=sealed_id)],
                    )
                ),
            )
    assert inactive.value.code == "MODEL_UNAVAILABLE"
    async with claimed_transaction(factory, user) as session:
        assert await _message_count(session, spirit_id) == 0

    owned_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES ("
                ":id, :spirit_id, 'preference', '用户希望被叫阿年', 90, 0.96"
                ")"
            ),
            {"id": owned_id, "spirit_id": spirit_id},
        )
        settled = await settle_chat_turn(
            session,
            user,
            _chat_request(),
            now=NOW,
            provider=_CitedProvider(
                ChatOutput(
                    reply=FAKE_REPLY,
                    citations=[ChatCitation(type="memory", id=owned_id)],
                )
            ),
        )
        stored_refs = await session.scalar(
            text("SELECT source_refs FROM public.messages WHERE id = :id"),
            {"id": settled.spirit_message_id},
        )
    assert settled.spirit_content == FAKE_REPLY
    assert settled.spirit_source_refs[0].id == owned_id
    assert settled.spirit_source_refs[0].type == "memory"
    assert stored_refs[0]["type"] == "memory"
    assert str(stored_refs[0]["id"]) == str(owned_id)
