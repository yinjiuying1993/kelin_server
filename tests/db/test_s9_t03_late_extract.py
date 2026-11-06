"""S9-T03: a control action beats a late extract. The old task cannot revive a row."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.types import ExtractInput, ExtractMemoryDraft, ExtractOutput
from app.repositories import memory_recall as recall_repo
from app.schemas.memory import MemoryPatchRequest
from app.services.central import execute_memory_control, publish_dialogue_completed
from app.services.memory.extract_worker import process_due_extracts
from app.services.memory.window_service import DialogueCompletedFact
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
SEALED = "窗边茶"
LATE = "迟到的新事实"


class _LateExtract:
    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        return ExtractOutput(
            memories=[
                ExtractMemoryDraft(
                    type="preference",
                    summary=SEALED,
                    normalized_topic_key="preference:user:item:sealed",
                    source_quote=SEALED,
                    signal="explicit",
                    confidence=0.9,
                ),
                ExtractMemoryDraft(
                    type="knowledge",
                    summary=LATE,
                    normalized_topic_key="knowledge:user:fact:late",
                    source_quote=LATE,
                    signal="teachable",
                    confidence=0.9,
                ),
            ]
        )


def test_control_beats_late_extract() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_control_wins(url))


async def _assert_control_wins(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=4, max_overflow=4)
    factory = create_session_factory(engine)
    await _ordered_seal_discards_late_task(url, factory)
    await _concurrent_seal_is_not_revived(url, factory)
    await engine.dispose()


async def _ordered_seal_discards_late_task(url: str, factory) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    memory_id, window_id = await _seed(url, user_id, spirit_id, "S9T3ABCD")
    user = CurrentUser(id=user_id)
    await _publish(factory, user, spirit_id, window_id)
    sealed = await _seal(factory, user, memory_id)
    assert sealed.snapshot_version == 2

    ticks = await process_due_extracts(factory, now=NOW, provider=_LateExtract())  # type: ignore[arg-type]
    assert len(ticks) == 1
    assert ticks[0].outcome == "discarded"
    assert ticks[0].status == "extracted"
    assert await _rows(url, spirit_id) == [("sealed", SEALED)]
    assert await _recall_ids(factory, user, spirit_id) == set()
    assert await _versions(url, spirit_id) == (2, 2)

    again = await process_due_extracts(
        factory, now=NOW, provider=_LateExtract()  # type: ignore[arg-type]
    )
    assert again == ()
    assert await _rows(url, spirit_id) == [("sealed", SEALED)]
    assert await _versions(url, spirit_id) == (2, 2)


async def _concurrent_seal_is_not_revived(url: str, factory) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    memory_id, window_id = await _seed(url, user_id, spirit_id, "S9T3EFGH")
    user = CurrentUser(id=user_id)
    await _publish(factory, user, spirit_id, window_id)
    before = await _versions(url, spirit_id)

    sealed, ticks = await asyncio.gather(
        _seal(factory, user, memory_id),
        process_due_extracts(factory, now=NOW, provider=_LateExtract()),  # type: ignore[arg-type]
    )
    assert sealed.snapshot_version >= before[0]
    assert ticks
    status, summary = await _row(url, memory_id)
    assert (status, summary) == ("sealed", SEALED)
    assert memory_id not in await _recall_ids(factory, user, spirit_id)
    snapshot, version = await _versions(url, spirit_id)
    assert snapshot == version
    assert snapshot >= sealed.snapshot_version
    assert snapshot >= before[0]


async def _publish(factory, user: CurrentUser, spirit_id: uuid.UUID, window_id: uuid.UUID) -> None:
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


async def _seal(factory, user: CurrentUser, memory_id: uuid.UUID):
    async with claimed_transaction(factory, user) as session:
        return await execute_memory_control(
            session,
            user,
            MemoryPatchRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "expected_version": 1, "action": "seal"}
            ),
            memory_id=memory_id,
            now=NOW,
        )


async def _seed(
    url: str, user_id: uuid.UUID, spirit_id: uuid.UUID, invite: str
) -> tuple[uuid.UUID, uuid.UUID]:
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
                ":id, :user_id, :client_id, 'warm', :invite, '未名', 'whelp', "
                "50, 50, 50, 50, 50, 0)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": invite,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'preference', :summary, 80, 0.900)"
            ),
            {"id": memory_id, "spirit_id": spirit_id, "summary": SEALED},
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
                "'user', :content, 'text', 'accepted')"
            ),
            {
                "id": user_message,
                "spirit_id": spirit_id,
                "window_id": window_id,
                "client_id": uuid.uuid4(),
                "content": SEALED,
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


async def _rows(url: str, spirit_id: uuid.UUID) -> list[tuple[str, str]]:
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


async def _row(url: str, memory_id: uuid.UUID) -> tuple[str, str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, summary FROM public.memories WHERE id = :id"),
                {"id": memory_id},
            )
        ).one()
    await engine.dispose()
    return str(row.status), str(row.summary)


async def _versions(url: str, spirit_id: uuid.UUID) -> tuple[int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT snapshot_version, version FROM public.spirits WHERE id = :id"),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return int(row[0]), int(row[1])


async def _recall_ids(
    factory: async_sessionmaker[AsyncSession], user: CurrentUser, spirit_id: uuid.UUID
) -> set[uuid.UUID]:
    async with claimed_transaction(factory, user) as session:
        rows = await recall_repo.fetch_prompt_memories(
            session, owner_id=user.id, spirit_id=spirit_id
        )
    return {row.id for row in rows}
