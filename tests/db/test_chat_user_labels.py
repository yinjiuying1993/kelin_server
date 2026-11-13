"""Home turns put agreement and teaching in the pack without a new mode."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.types import ChatInput, ChatOutput
from app.schemas.chat import ChatRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import settle_chat_turn
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


class _EchoMode:
    source = "stub"

    def __init__(self) -> None:
        self.modes: list[str] = []

    async def chat(self, value: ChatInput) -> ChatOutput:
        mode = "companion"
        for line in value.emotion_context.splitlines():
            if line.startswith("mode: "):
                mode = line.split(": ", 1)[1].strip()
                break
        self.modes.append(mode)
        return ChatOutput(reply="知道了。", intent=mode)  # type: ignore[arg-type]

    async def search(self, value: object) -> list[object]:
        del value
        return []


def test_pack_mode_follows_agreement_teaching_and_knowledge() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_modes(url))


async def _assert_modes(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    auth = create_async_engine(url)
    async with auth.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": owner})
    await auth.dispose()
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(
            session,
            user,
            CreateSpiritRequest.model_validate(
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
            ),
        )
        spirit_id = created.spirit_id
    provider = _EchoMode()
    for index, content in enumerate(("我们约定明天见面", "我教你先写标题", "辞职流程是什么")):
        async with claimed_transaction(factory, user) as session:
            await settle_chat_turn(
                session,
                user,
                _request(content),
                now=NOW + timedelta(seconds=index),
                provider=provider,  # type: ignore[arg-type]
            )
    assert provider.modes == ["companion", "companion", "knowledge"]
    check = create_async_engine(url)
    async with check.connect() as conn:
        memories = await conn.scalar(
            text("SELECT count(*) FROM public.memories WHERE spirit_id = :id"),
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
    await check.dispose()
    assert int(memories or 0) == 0
    assert int(tasks or 0) == 1
    await engine.dispose()


def _request(content: str) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(uuid.uuid4()),
            "content": content,
            "source": "text",
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 12,
                "weather": "cloudy",
                "city": None,
            },
        }
    )
