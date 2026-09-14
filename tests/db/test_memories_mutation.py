from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import (
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.repositories import memory as memory_repo
from app.schemas.memory import MemoryClearRequest, MemoryDeleteRequest, MemoryPatchRequest
from app.services.memory import (
    clear_owned_memories,
    delete_owned_memory,
    patch_owned_memory,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE_A = "MEMTABCD"
INVITE_B = "MEMTEFGH"
REPO = Path(__file__).resolve().parents[2] / "app" / "repositories" / "memory.py"


def test_mutation_sql_does_not_use_offset() -> None:
    source = REPO.read_text(encoding="utf-8")
    assert "OFFSET" not in source.upper()


def test_correct_seal_delete_clear_are_atomic_and_owner_scoped() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_mutations(url))


async def _assert_mutations(url: str) -> None:
    owner = uuid.uuid4()
    other = uuid.uuid4()
    spirit_id = uuid.uuid4()
    other_spirit = uuid.uuid4()
    memory_id = uuid.uuid4()
    sealed_id = uuid.uuid4()
    extra_id = uuid.uuid4()
    sample_id = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (:a), (:b)"),
            {"a": owner, "b": other},
        )
        await _insert_spirit(conn, spirit_id, owner, INVITE_A)
        await _insert_spirit(conn, other_spirit, other, INVITE_B)
        await _insert_memory(conn, memory_id, spirit_id, "preference", "旧称呼")
        await _insert_memory(conn, sealed_id, spirit_id, "knowledge", "旧知识")
        await _insert_memory(conn, extra_id, spirit_id, "speech", "口头禅")
        await conn.execute(
            text(
                "INSERT INTO public.style_samples (id, spirit_id, kind, text) "
                "VALUES (:id, :spirit_id, 'user_filler', '嗯')"
            ),
            {"id": sample_id, "spirit_id": spirit_id},
        )
        await _insert_memory(conn, uuid.uuid4(), other_spirit, "preference", "他人")
    await engine.dispose()

    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    owner_user = CurrentUser(id=owner)
    other_user = CurrentUser(id=other)

    async with claimed_transaction(factory, owner_user) as session:
        corrected = await patch_owned_memory(
            session,
            owner_user,
            memory_id,
            MemoryPatchRequest.model_validate(
                {
                    "client_id": str(uuid.uuid4()),
                    "expected_version": 1,
                    "action": "correct",
                    "summary": "请叫我阿年",
                }
            ),
        )
        assert corrected.memory is not None
        assert corrected.memory.summary == "请叫我阿年"
        assert corrected.memory.version == 2
        assert corrected.memory.status == "active"
        assert corrected.snapshot_version >= 2

    async with claimed_transaction(factory, owner_user) as session:
        try:
            await patch_owned_memory(
                session,
                owner_user,
                memory_id,
                MemoryPatchRequest.model_validate(
                    {
                        "client_id": str(uuid.uuid4()),
                        "expected_version": 1,
                        "action": "correct",
                        "summary": "请叫我阿年",
                    }
                ),
            )
        except ApiError as exc:
            assert exc.code == "CONFLICT"
        else:
            raise AssertionError("stale expected_version must conflict")

    same_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        first_seal = await patch_owned_memory(
            session,
            owner_user,
            sealed_id,
            MemoryPatchRequest.model_validate(
                {
                    "client_id": str(same_client),
                    "expected_version": 1,
                    "action": "seal",
                }
            ),
        )
        assert first_seal.memory is not None
        assert first_seal.memory.status == "sealed"
        replay_seal = await patch_owned_memory(
            session,
            owner_user,
            sealed_id,
            MemoryPatchRequest.model_validate(
                {
                    "client_id": str(same_client),
                    "expected_version": 1,
                    "action": "seal",
                }
            ),
        )
        assert replay_seal.memory is not None
        assert replay_seal.memory.version == first_seal.memory.version

    async with claimed_transaction(factory, owner_user) as session:
        try:
            await patch_owned_memory(
                session,
                owner_user,
                sealed_id,
                MemoryPatchRequest.model_validate(
                    {
                        "client_id": str(uuid.uuid4()),
                        "expected_version": first_seal.memory.version if first_seal.memory else 2,
                        "action": "correct",
                        "summary": "不该纠正",
                    }
                ),
            )
        except ApiError as exc:
            assert exc.code == "MEMORY_NOT_ACTIVE"
        else:
            raise AssertionError("sealed memory cannot be corrected")

    async with claimed_transaction(factory, other_user) as session:
        try:
            await patch_owned_memory(
                session,
                other_user,
                memory_id,
                MemoryPatchRequest.model_validate(
                    {
                        "client_id": str(uuid.uuid4()),
                        "expected_version": 2,
                        "action": "seal",
                    }
                ),
            )
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("foreign memory must be not found")

    delete_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        deleted = await delete_owned_memory(
            session,
            owner_user,
            extra_id,
            MemoryDeleteRequest.model_validate(
                {"client_id": str(delete_client), "expected_version": 1}
            ),
        )
        assert deleted.tombstone is not None
        assert deleted.memory is not None
        assert deleted.memory.status == "deleted"
        replay_delete = await delete_owned_memory(
            session,
            owner_user,
            extra_id,
            MemoryDeleteRequest.model_validate(
                {"client_id": str(delete_client), "expected_version": 1}
            ),
        )
        assert replay_delete.tombstone is not None
        assert replay_delete.tombstone.deleted_at == deleted.tombstone.deleted_at
        again = await delete_owned_memory(
            session,
            owner_user,
            extra_id,
            MemoryDeleteRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "expected_version": 9}
            ),
        )
        assert again.memory is not None
        assert again.memory.status == "deleted"

    try:
        async with claimed_transaction(factory, owner_user) as session:
            await memory_repo.clear_undeleted_memories(session, spirit_id=spirit_id)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    async with claimed_transaction(factory, owner_user) as session:
        active = await session.scalar(
            text(
                "SELECT count(*) FROM public.memories "
                "WHERE spirit_id = :spirit_id AND status = 'active'"
            ),
            {"spirit_id": spirit_id},
        )
    assert int(active or 0) >= 1

    clear_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        cleared = await clear_owned_memories(
            session,
            owner_user,
            MemoryClearRequest.model_validate(
                {"client_id": str(clear_client), "confirm": "CLEAR_ALL_MEMORIES"}
            ),
        )
        assert cleared.kind == "clear"
        remaining = await session.scalar(
            text(
                "SELECT count(*) FROM public.memories "
                "WHERE spirit_id = :spirit_id AND status <> 'deleted'"
            ),
            {"spirit_id": spirit_id},
        )
        samples = await session.scalar(
            text(
                "SELECT count(*) FROM public.style_samples "
                "WHERE spirit_id = :spirit_id AND status = 'active'"
            ),
            {"spirit_id": spirit_id},
        )
    async with claimed_transaction(factory, other_user) as session:
        other_alive = await session.scalar(
            text(
                "SELECT count(*) FROM public.memories "
                "WHERE spirit_id = :spirit_id AND status = 'active'"
            ),
            {"spirit_id": other_spirit},
        )
    assert int(remaining or 0) == 0
    assert int(samples or 0) == 0
    assert int(other_alive or 0) == 1
    await runtime.dispose()


async def _insert_spirit(conn: Any, spirit_id: uuid.UUID, user_id: uuid.UUID, invite: str) -> None:
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
            "user_id": user_id,
            "client_id": uuid.uuid4(),
            "invite": invite,
        },
    )


async def _insert_memory(
    conn: Any,
    memory_id: uuid.UUID,
    spirit_id: uuid.UUID,
    memory_type: str,
    summary: str,
) -> None:
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
            "summary": summary,
            "created_at": datetime(2026, 3, 1, tzinfo=UTC) + timedelta(seconds=1),
        },
    )
