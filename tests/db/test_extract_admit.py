"""Worker admits extract drafts and does not write a successful window twice."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.types import ExtractInput, ExtractMemoryDraft, ExtractOutput
from app.services.central import publish_dialogue_completed
from app.services.memory.extract_worker import process_due_extracts
from app.services.memory.window_service import DialogueCompletedFact
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class _Script:
    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, value: ExtractInput) -> ExtractOutput:
        self.calls += 1
        blob = "\n".join(turn.content for turn in value.turns)
        if "开玩笑" in blob:
            summary, confidence, quote = "喜欢窗边", 0.95, "喜欢窗边"
        elif "喝茶" in blob:
            summary, confidence, quote = "喜欢喝茶", 0.5, "喜欢喝茶"
        elif "想去火星" in blob:
            summary, confidence, quote = "想去火星", 0.95, "想去火星"
        else:
            summary, confidence, quote = "喜欢窗边", 0.95, "记住我喜欢窗边"
        return ExtractOutput(
            memories=[
                ExtractMemoryDraft(
                    type="preference",
                    summary=summary,
                    normalized_topic_key="preference:user:seat:window",
                    source_quote=quote,
                    signal="explicit",
                    confidence=confidence,
                )
            ]
        )


def test_worker_drops_rejected_drafts_and_does_not_rewrite() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_gates(url))


async def _assert_gates(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed_spirit(url, user_id, spirit_id)
    windows = (
        ("哈哈我喜欢窗边，开玩笑", "记下了。"),
        ("记住我喜欢喝茶", "记下了。"),
        ("记住我喜欢安静", "想去火星"),
        ("记住我喜欢窗边", "记下了。"),
    )
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    for user_text, spirit_text in windows:
        window_id = uuid.uuid4()
        await _seed_window(
            url,
            spirit_id=spirit_id,
            window_id=window_id,
            user_text=user_text,
            spirit_text=spirit_text,
        )
        async with claimed_transaction(factory, user) as session:
            created = await publish_dialogue_completed(
                session,
                user,
                DialogueCompletedFact(
                    event_id=uuid.uuid4(),
                    spirit_id=spirit_id,
                    conversation_window_id=window_id,
                    occurred_at=NOW,
                ),
            )
        assert created is not None and created.task_created is True
    provider = _Script()
    ticks = await process_due_extracts(factory, now=NOW, provider=provider)  # type: ignore[arg-type]
    assert len(ticks) == 4
    assert {tick.status for tick in ticks} == {"extracted"}
    assert await _summaries(url, spirit_id) == ["喜欢窗边"]
    again = await process_due_extracts(factory, now=NOW, provider=provider)  # type: ignore[arg-type]
    assert again == ()
    assert provider.calls == 4
    assert await _summaries(url, spirit_id) == ["喜欢窗边"]
    await engine.dispose()


async def _seed_spirit(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, hatched_at"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, 50, 50, 50, 50, 50, :now)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": "ADMT2345",
                "now": NOW,
            },
        )
    await engine.dispose()


async def _seed_window(
    url: str,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    user_text: str,
    spirit_text: str,
) -> None:
    user_message = uuid.uuid4()
    spirit_message = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.conversation_windows ("
                "id, spirit_id, start_message_id, end_message_id, "
                "user_round_count, onboarding, status"
                ") VALUES ("
                ":id, :spirit_id, :start_id, :end_id, 3, false, 'ready')"
            ),
            {
                "id": window_id,
                "spirit_id": spirit_id,
                "start_id": user_message,
                "end_id": spirit_message,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, conversation_window_id, client_id, role, content, source, status"
                ") VALUES ("
                ":id, :spirit_id, :window_id, :client_id, 'user', :content, 'text', 'accepted')"
            ),
            {
                "id": user_message,
                "spirit_id": spirit_id,
                "window_id": window_id,
                "client_id": uuid.uuid4(),
                "content": user_text,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, conversation_window_id, role, content, source, status"
                ") VALUES ("
                ":id, :spirit_id, :window_id, 'spirit', :content, 'text', 'accepted')"
            ),
            {
                "id": spirit_message,
                "spirit_id": spirit_id,
                "window_id": window_id,
                "content": spirit_text,
            },
        )
    await engine.dispose()


async def _summaries(url: str, spirit_id: uuid.UUID) -> list[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT summary FROM public.memories "
                    "WHERE spirit_id = :id ORDER BY created_at ASC, id ASC"
                ),
                {"id": spirit_id},
            )
        ).all()
    await engine.dispose()
    return [str(row.summary) for row in rows]
