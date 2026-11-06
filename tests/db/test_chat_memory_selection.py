"""A whelp pack holds at most three memories, and only ones this turn can use."""

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
UNRELATED = "高显著天文台观测"


class _Pack:
    source = "stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.seen: list[ChatInput] = []

    async def chat(self, value: ChatInput) -> ChatOutput:
        self.seen.append(value)
        mode = "companion"
        for line in value.emotion_context.splitlines():
            if line.startswith("mode: "):
                mode = line.split(": ", 1)[1].strip()
        return ChatOutput(reply=self.reply, intent=mode)  # type: ignore[arg-type]

    async def search(self, value: object) -> list[object]:
        del value
        return []


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {"document_version": "2026-09", "explicitly_accepted": True},
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


def test_whelp_pack_drops_unrelated_memories_and_outside_facts() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_pack(url))


async def _assert_pack(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        spirit_id = created.spirit_id
    async with claimed_transaction(factory, user) as session:
        for summary in (
            "周末爬山路线甲",
            "周末爬山路线乙",
            "周末爬山路线丙",
            "周末爬山路线丁",
            "库房清单甲乙丙",
            "库房清单丁戊己",
            "库房清单庚辛壬",
            "库房清单癸子丑",
            "库房清单寅卯辰",
            UNRELATED,
        ):
            await session.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence"
                    ") VALUES ("
                    ":id, :spirit_id, 'preference', :summary, :salience, 0.96"
                    ")"
                ),
                {
                    "id": uuid.uuid4(),
                    "spirit_id": spirit_id,
                    "summary": summary,
                    "salience": 99 if summary == UNRELATED else 20,
                },
            )

    shown = _Pack("知道了。")
    async with claimed_transaction(factory, user) as session:
        await settle_chat_turn(
            session,
            user,
            _chat_request("周末爬山"),
            now=NOW,
            provider=shown,  # type: ignore[arg-type]
        )
    assert shown.seen
    pack = shown.seen[0]
    summaries = {item.summary for item in pack.memories}
    assert len(pack.memories) == 3
    assert summaries <= {
        "周末爬山路线甲",
        "周末爬山路线乙",
        "周末爬山路线丙",
        "周末爬山路线丁",
    }
    assert UNRELATED not in pack.emotion_context

    leaked = _Pack(f"{UNRELATED}。")
    async with claimed_transaction(factory, user) as session:
        settled = await settle_chat_turn(
            session,
            user,
            _chat_request("周末爬山"),
            now=NOW + timedelta(seconds=1),
            provider=leaked,  # type: ignore[arg-type]
        )
    assert len(leaked.seen) == 2
    assert UNRELATED not in settled.spirit_content
    assert settled.spirit_source_refs == ()


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()
