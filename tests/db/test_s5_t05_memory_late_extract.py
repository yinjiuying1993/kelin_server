"""S5-T05: delete wins; a late extract does not re-enter recall or move snapshot."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.types import ExtractInput, ExtractMemoryDraft, ExtractOutput
from app.repositories import memory_recall as recall_repo
from app.schemas.memory import MemoryDeleteRequest
from app.services.central import execute_memory_control, publish_dialogue_completed
from app.services.memory.extract_worker import process_due_extracts
from app.services.memory.window_service import (
    EXTRACT_EVENT,
    DialogueCompletedFact,
    extract_dedupe_key,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "T5LAT234"


class _LateExtract:
    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        return ExtractOutput(
            memories=[
                ExtractMemoryDraft(
                    type="preference",
                    summary="叫我阿年",
                    normalized_topic_key="preference:user:name:nian",
                    source_quote="叫我阿年",
                    signal="explicit",
                    confidence=0.9,
                ),
                ExtractMemoryDraft(
                    type="knowledge",
                    summary="迟到的新事实",
                    normalized_topic_key="knowledge:user:fact:late",
                    source_quote="迟到的新事实",
                    signal="teachable",
                    confidence=0.9,
                ),
            ]
        )


async def _seed(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    memory_id = uuid.uuid4()
    window_id = uuid.uuid4()
    user_message = uuid.uuid4()
    spirit_message = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, name, stage, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, bond"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, '雾生', 'whelp', "
                "50, 50, 50, 50, 50, 12)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'preference', '叫我阿年', 80, 0.900)"
            ),
            {"id": memory_id, "spirit_id": spirit_id},
        )
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
                ":id, :spirit_id, :window_id, :client_id, "
                "'user', '今天想被叫阿年', 'text', 'accepted')"
            ),
            {
                "id": user_message,
                "spirit_id": spirit_id,
                "window_id": window_id,
                "client_id": uuid.uuid4(),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, conversation_window_id, role, content, source, status"
                ") VALUES ("
                ":id, :spirit_id, :window_id, 'spirit', '记下了。', 'text', 'accepted')"
            ),
            {"id": spirit_message, "spirit_id": spirit_id, "window_id": window_id},
        )
    await engine.dispose()
    return memory_id, window_id


async def _spirit_numbers(url: str, spirit_id: uuid.UUID) -> tuple[int, int, str, int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT snapshot_version, version, stage, closeness, bond "
                    "FROM public.spirits WHERE id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return int(row[0]), int(row[1]), str(row[2]), int(row[3]), int(row[4])


async def _task_row(url: str, window_id: uuid.UUID) -> tuple[str, str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT dedupe_key, payload->>'snapshot_version' "
                    "FROM public.outbox_events "
                    "WHERE event_type = :event_type AND aggregate_id = :window_id"
                ),
                {"event_type": EXTRACT_EVENT, "window_id": window_id},
            )
        ).one()
    await engine.dispose()
    return str(row[0]), str(row[1])


async def _memory_rows(url: str, spirit_id: uuid.UUID) -> list[tuple[str, str]]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT status, summary FROM public.memories "
                    "WHERE spirit_id = :spirit_id ORDER BY created_at, id"
                ),
                {"spirit_id": spirit_id},
            )
        ).all()
    await engine.dispose()
    return [(str(row.status), str(row.summary)) for row in rows]


async def _recall_ids(factory, user: CurrentUser, spirit_id: uuid.UUID) -> set[uuid.UUID]:
    async with claimed_transaction(factory, user) as session:
        rows = await recall_repo.fetch_prompt_memories(
            session, owner_id=user.id, spirit_id=spirit_id
        )
    return {row.id for row in rows}


def test_deleted_memory_stays_out_of_recall_after_late_extract() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_late_extract(url))


async def _assert_late_extract(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    memory_id, window_id = await _seed(url, user_id, spirit_id)
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
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
    assert await _task_row(url, window_id) == (extract_dedupe_key(window_id), "1")

    async with claimed_transaction(factory, user) as session:
        deleted = await execute_memory_control(
            session,
            user,
            MemoryDeleteRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "expected_version": 1}
            ),
            memory_id=memory_id,
            now=NOW,
        )
    assert deleted.snapshot_version == 2
    assert await _recall_ids(factory, user, spirit_id) == set()
    assert await _spirit_numbers(url, spirit_id) == (2, 2, "whelp", 50, 12)

    ticks = await process_due_extracts(
        factory, now=NOW, provider=_LateExtract()  # type: ignore[arg-type]
    )
    assert ticks
    assert ticks[0].outcome == "discarded"
    assert ticks[0].status == "extracted"
    assert await _recall_ids(factory, user, spirit_id) == set()
    assert await _memory_rows(url, spirit_id) == [("deleted", "叫我阿年")]
    assert await _spirit_numbers(url, spirit_id) == (2, 2, "whelp", 50, 12)

    async with claimed_transaction(factory, user) as session:
        again = await publish_dialogue_completed(
            session,
            user,
            DialogueCompletedFact(
                event_id=uuid.uuid4(),
                spirit_id=spirit_id,
                conversation_window_id=window_id,
                occurred_at=NOW,
            ),
        )
    assert again is not None and again.task_created is False
    assert await _task_row(url, window_id) == (extract_dedupe_key(window_id), "1")
    await engine.dispose()
