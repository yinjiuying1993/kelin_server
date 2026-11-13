"""E5: a chat turn sends the settled emotion budget and does not spend energy."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

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

NOW = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)


class _Capture:
    source = "stub"

    def __init__(self) -> None:
        self.seen: ChatInput | None = None

    async def chat(self, value: ChatInput) -> ChatOutput:
        self.seen = value
        return ChatOutput(reply="嗯。", intent="companion", citations=[], safety="allow")


def test_chat_prompt_uses_settled_budget() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_prompt(url))


async def _assert_prompt(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    auth = create_async_engine(url)
    async with auth.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": owner})
    await auth.dispose()
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(
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
    capture = _Capture()
    body = ChatRequest.model_validate(
        {
            "client_message_id": str(uuid.uuid4()),
            "content": "你好",
            "source": "text",
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 12,
                "weather": "cloudy",
                "city": None,
            },
        }
    )
    async with claimed_transaction(factory, user) as session:
        settled = await settle_chat_turn(session, user, body, now=NOW, provider=capture)  # type: ignore[arg-type]
    assert capture.seen is not None
    assert "primary_label:" not in capture.seen.emotion_context
    assert "sentence_cap:" not in capture.seen.emotion_context
    assert "mode: companion" in capture.seen.emotion_context
    assert "reply_length_budget:" not in capture.seen.emotion_context
    assert "weather" not in capture.seen.emotion_context
    check = create_async_engine(url)
    async with check.connect() as conn:
        energy = await conn.scalar(
            text(
                "SELECT a.energy FROM public.spirit_awareness a "
                "JOIN public.spirits s ON s.id = a.spirit_id WHERE s.user_id = :id"
            ),
            {"id": owner},
        )
    await check.dispose()
    assert int(energy) == 79
    assert settled.room is not None
    assert settled.room.stage_image_key == "whelp_image"
    assert settled.room.letter_key is None
    assert settled.room.text_status == "home"
    assert settled.room.status == "home"
    await engine.dispose()
