from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.repositories import memory as memory_repo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE_A = "MEMTABCD"
INVITE_B = "MEMTEFGH"
REPO = Path(__file__).resolve().parents[2] / "app" / "repositories" / "memory.py"
ACTIVE_TYPES = ("preference", "knowledge", "emotion", "relation", "speech", "sight")


def test_repository_sql_is_keyset_without_offset() -> None:
    source = REPO.read_text(encoding="utf-8")
    assert "OFFSET" not in source.upper()
    assert "LIMIT :fetch_limit" in source
    assert "status = 'active'" in source
    assert "status = 'deleted'" in source
    assert "ORDER BY m.created_at DESC, m.id DESC" in source


def test_keyset_filters_tombstones_and_owner_isolation() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_keyset(url))


async def _assert_keyset(url: str) -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    spirit_id = uuid.uuid4()
    other_spirit = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (:a), (:b)"),
            {"a": owner, "b": other},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, 50, 50, 50, 50, 50)"
            ),
            {
                "id": spirit_id,
                "user_id": owner,
                "client_id": uuid.uuid4(),
                "invite": INVITE_A,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, 50, 50, 50, 50, 50)"
            ),
            {
                "id": other_spirit,
                "user_id": other,
                "client_id": uuid.uuid4(),
                "invite": INVITE_B,
            },
        )
        base = datetime(2026, 2, 1, tzinfo=UTC)
        active_ids: list[uuid.UUID] = []
        for index, memory_type in enumerate(ACTIVE_TYPES):
            memory_id = uuid.uuid4()
            active_ids.append(memory_id)
            await conn.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence, created_at"
                    ") VALUES ("
                    ":id, :spirit_id, :type, :summary, 80, 0.900, :created_at)"
                ),
                {
                    "id": memory_id,
                    "spirit_id": spirit_id,
                    "type": memory_type,
                    "summary": f"刻痕{index + 1}",
                    "created_at": base + timedelta(seconds=index),
                },
            )
        sealed_id = uuid.uuid4()
        deleted_id = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, status, sealed_at"
                ") VALUES ("
                ":id, :spirit_id, 'knowledge', '已封存', 80, 0.900, 'sealed', now())"
            ),
            {"id": sealed_id, "spirit_id": spirit_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, status, deleted_at"
                ") VALUES ("
                ":id, :spirit_id, 'speech', '已删除正文', 80, 0.900, 'deleted', now())"
            ),
            {"id": deleted_id, "spirit_id": spirit_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'preference', '他人', 70, 0.800)"
            ),
            {"id": uuid.uuid4(), "spirit_id": other_spirit},
        )
    await engine.dispose()

    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    owner_user = CurrentUser(id=owner)
    other_user = CurrentUser(id=other)
    async with claimed_read_transaction(factory, owner_user) as session:
        snapshot = await memory_repo.fetch_transaction_now(session)
        owned = await memory_repo.fetch_owned_spirit_id(session, owner)
        assert owned == spirit_id
        first = await memory_repo.list_active_memories_keyset(
            session,
            spirit_id=spirit_id,
            snapshot_at=snapshot,
            fetch_limit=3,
            types=None,
        )
        assert len(first) == 3
        assert all(row.status == "active" for row in first)
        assert sealed_id not in {row.id for row in first}
        assert deleted_id not in {row.id for row in first}
        rest = await memory_repo.list_active_memories_keyset(
            session,
            spirit_id=spirit_id,
            snapshot_at=snapshot,
            fetch_limit=4,
            types=None,
            cursor_time=first[1].created_at,
            cursor_id=first[1].id,
        )
        tombstones = await memory_repo.list_deleted_tombstones(
            session,
            spirit_id=spirit_id,
            snapshot_at=snapshot,
            types=None,
        )
        relationship = await memory_repo.list_active_memories_keyset(
            session,
            spirit_id=spirit_id,
            snapshot_at=snapshot,
            fetch_limit=20,
            types=("preference", "relation", "emotion"),
        )
        knowledge = await memory_repo.list_active_memories_keyset(
            session,
            spirit_id=spirit_id,
            snapshot_at=snapshot,
            fetch_limit=20,
            types=("knowledge",),
        )
        speech_tombs = await memory_repo.list_deleted_tombstones(
            session,
            spirit_id=spirit_id,
            snapshot_at=snapshot,
            types=("speech",),
        )
        knowledge_tombs = await memory_repo.list_deleted_tombstones(
            session,
            spirit_id=spirit_id,
            snapshot_at=snapshot,
            types=("knowledge",),
        )
    first_ids = [row.id for row in first[:2]]
    rest_ids = [row.id for row in rest]
    assert not set(first_ids) & set(rest_ids)
    assert len(first_ids) + len(rest_ids) == 6
    assert set(first_ids + rest_ids) == set(active_ids)
    assert [row.id for row in tombstones] == [deleted_id]
    assert {row.type for row in relationship} == {"preference", "relation", "emotion"}
    assert len(relationship) == 3
    assert {row.type for row in knowledge} == {"knowledge"}
    assert sealed_id not in {row.id for row in knowledge}
    assert [row.id for row in speech_tombs] == [deleted_id]
    assert knowledge_tombs == []

    async with claimed_read_transaction(factory, other_user) as session:
        other_owned = await memory_repo.fetch_owned_spirit_id(session, other)
        assert other_owned == other_spirit
        leaked = await memory_repo.list_active_memories_keyset(
            session,
            spirit_id=spirit_id,
            snapshot_at=await memory_repo.fetch_transaction_now(session),
            fetch_limit=20,
            types=None,
        )
        other_items = await memory_repo.list_active_memories_keyset(
            session,
            spirit_id=other_spirit,
            snapshot_at=await memory_repo.fetch_transaction_now(session),
            fetch_limit=20,
            types=None,
        )
        leaked_tombs = await memory_repo.list_deleted_tombstones(
            session,
            spirit_id=spirit_id,
            snapshot_at=await memory_repo.fetch_transaction_now(session),
            types=None,
        )
    assert leaked == []
    assert leaked_tombs == []
    assert {row.id for row in other_items}.isdisjoint(set(active_ids))

    async with claimed_read_transaction(factory, None) as session:
        unclaimed = await memory_repo.list_active_memories_keyset(
            session,
            spirit_id=spirit_id,
            snapshot_at=await memory_repo.fetch_transaction_now(session),
            fetch_limit=20,
            types=None,
        )
        unclaimed_owned = await memory_repo.fetch_owned_spirit_id(session, owner)
    assert unclaimed == []
    assert unclaimed_owned is None
    await runtime.dispose()
