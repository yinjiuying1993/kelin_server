"""S5-T04: control bumps snapshot now; next recall drops sealed/deleted."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.repositories import memory_recall as recall_repo
from app.schemas.memory import MemoryClearRequest, MemoryDeleteRequest, MemoryPatchRequest
from app.services.central import execute_memory_control
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "T5CTL234"


async def _seed(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    first = uuid.uuid4()
    second = uuid.uuid4()
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
        for memory_id, memory_type, summary in (
            (first, "preference", "叫我阿年"),
            (second, "knowledge", "面试用STAR"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence"
                    ") VALUES (:id, :spirit_id, :type, :summary, 80, 0.900)"
                ),
                {
                    "id": memory_id,
                    "spirit_id": spirit_id,
                    "type": memory_type,
                    "summary": summary,
                },
            )
    await engine.dispose()
    return first, second


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


async def _recall_ids(factory, user: CurrentUser, spirit_id: uuid.UUID) -> set[uuid.UUID]:
    async with claimed_transaction(factory, user) as session:
        rows = await recall_repo.fetch_prompt_memories(
            session, owner_id=user.id, spirit_id=spirit_id
        )
    return {row.id for row in rows}


def test_control_bumps_snapshot_and_drops_recall_immediately() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_control(url))


async def _assert_control(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    sealed_id, active_id = await _seed(url, user_id, spirit_id)
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    seal_client = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        sealed = await execute_memory_control(
            session,
            user,
            MemoryPatchRequest.model_validate(
                {
                    "client_id": str(seal_client),
                    "expected_version": 1,
                    "action": "seal",
                }
            ),
            memory_id=sealed_id,
            now=NOW,
        )
    assert sealed.snapshot_version == 2
    assert sealed.settlement.memory is not None
    assert sealed.settlement.memory.status == "sealed"
    assert await _spirit_numbers(url, spirit_id) == (2, 2, "whelp", 50, 12)
    assert await _recall_ids(factory, user, spirit_id) == {active_id}

    async with claimed_transaction(factory, user) as session:
        replay = await execute_memory_control(
            session,
            user,
            MemoryPatchRequest.model_validate(
                {
                    "client_id": str(seal_client),
                    "expected_version": 1,
                    "action": "seal",
                }
            ),
            memory_id=sealed_id,
            now=NOW,
        )
    assert replay.snapshot_version == 2
    assert await _spirit_numbers(url, spirit_id) == (2, 2, "whelp", 50, 12)

    async with claimed_transaction(factory, user) as session:
        deleted = await execute_memory_control(
            session,
            user,
            MemoryDeleteRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "expected_version": 1}
            ),
            memory_id=active_id,
            now=NOW,
        )
    assert deleted.snapshot_version == 3
    assert deleted.settlement.tombstone is not None
    assert deleted.settlement.memory is not None
    assert deleted.settlement.memory.summary
    assert deleted.settlement.tombstone.id == active_id
    assert await _recall_ids(factory, user, spirit_id) == set()

    async with claimed_transaction(factory, user) as session:
        cleared = await execute_memory_control(
            session,
            user,
            MemoryClearRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "confirm": "CLEAR_ALL_MEMORIES"}
            ),
            memory_id=None,
            now=NOW,
        )
    assert cleared.snapshot_version == 4
    assert cleared.settlement.kind == "clear"
    assert await _recall_ids(factory, user, spirit_id) == set()
    assert await _spirit_numbers(url, spirit_id) == (4, 4, "whelp", 50, 12)
    await engine.dispose()
