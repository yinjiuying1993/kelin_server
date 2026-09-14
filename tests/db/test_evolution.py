from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.repositories import feed as feed_repo
from app.schemas.spirit import CreateSpiritRequest
from app.services.bootstrap import load_bootstrap_snapshot
from app.services.evolution import grant_pact_scholar_mark, maybe_advance_stage
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 11, 15, 0, tzinfo=UTC)


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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _insert_memory(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    memory_type: str,
    *,
    status: str = "active",
) -> uuid.UUID:
    memory_id = uuid.uuid4()
    sealed = "NOW()" if status == "sealed" else "NULL"
    deleted = "NOW()" if status == "deleted" else "NULL"
    await session.execute(
        text(
            "INSERT INTO public.memories ("
            "id, spirit_id, type, summary, salience, confidence, status, "
            "sealed_at, deleted_at"
            ") VALUES ("
            f":id, :spirit_id, :type, :summary, 70, 1, :status, {sealed}, {deleted}"
            ")"
        ),
        {
            "id": memory_id,
            "spirit_id": spirit_id,
            "type": memory_type,
            "summary": f"{memory_type} fact",
            "status": status,
        },
    )
    return memory_id


async def _insert_knowledge_feed(
    session: AsyncSession, *, owner_id: uuid.UUID, spirit_id: uuid.UUID
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.feeds ("
            "user_id, spirit_id, client_id, kind, payload, status, effect_applied_at"
            ") VALUES ("
            ":user_id, :spirit_id, :client_id, 'knowledge', "
            "CAST(:payload AS jsonb), 'accepted', :now"
            ")"
        ),
        {
            "user_id": owner_id,
            "spirit_id": spirit_id,
            "client_id": uuid.uuid4(),
            "payload": '{"text":"圆周率"}',
            "now": NOW,
        },
    )


def test_formed_boundary_sealed_exclusion_and_once() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_formed(url))


async def _assert_formed(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        await session.execute(
            text("UPDATE public.spirits SET bond = 20 WHERE id = :id"),
            {"id": created.spirit_id},
        )
        await _insert_memory(session, created.spirit_id, "preference")
        await _insert_memory(session, created.spirit_id, "knowledge")
        locked = await feed_repo.lock_owned_spirit(session, user.id)
        assert locked is not None
        first = await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=NOW)
        stage = await session.scalar(
            text("SELECT stage FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
    assert first.advances == ()
    assert stage == "whelp"

    async with claimed_transaction(factory, user) as session:
        sealed_id = await _insert_memory(session, created.spirit_id, "sight")
        await session.execute(
            text("UPDATE public.memories SET status = 'sealed', sealed_at = :now WHERE id = :id"),
            {"id": sealed_id, "now": NOW},
        )
        locked = await feed_repo.lock_owned_spirit(session, user.id)
        assert locked is not None
        sealed = await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=NOW)
        stage = await session.scalar(
            text("SELECT stage FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
    assert sealed.advances == ()
    assert stage == "whelp"

    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text("UPDATE public.memories SET status = 'active', sealed_at = NULL WHERE id = :id"),
            {"id": sealed_id},
        )
        locked = await feed_repo.lock_owned_spirit(session, user.id)
        assert locked is not None
        formed = await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=NOW)
        again = await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=NOW)
        stage = await session.scalar(
            text("SELECT stage FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
    assert [item.to_stage for item in formed.advances] == ["formed"]
    assert again.advances == ()
    assert stage == "formed"
    await engine.dispose()


def test_awake_boundary_and_no_second_event() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_awake(url))


async def _assert_awake(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        await session.execute(
            text("UPDATE public.spirits SET bond = 50, stage = 'formed' WHERE id = :id"),
            {"id": created.spirit_id},
        )
        await _insert_memory(session, created.spirit_id, "preference")
        await _insert_memory(session, created.spirit_id, "knowledge")
        await _insert_memory(session, created.spirit_id, "sight")
        await _insert_knowledge_feed(session, owner_id=user.id, spirit_id=created.spirit_id)
        locked = await feed_repo.lock_owned_spirit(session, user.id)
        assert locked is not None
        missing_lost = await maybe_advance_stage(
            session, owner_id=user.id, spirit_id=locked.id, now=NOW
        )
        stage = await session.scalar(
            text("SELECT stage FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
    assert missing_lost.advances == ()
    assert stage == "formed"

    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text("UPDATE public.spirits SET has_been_lost = true WHERE id = :id"),
            {"id": created.spirit_id},
        )
        locked = await feed_repo.lock_owned_spirit(session, user.id)
        assert locked is not None
        awake = await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=NOW)
        again = await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=NOW)
        stage = await session.scalar(
            text("SELECT stage FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
    assert [item.to_stage for item in awake.advances] == ["awake"]
    assert again.advances == ()
    assert stage == "awake"
    await engine.dispose()


def test_whelp_with_awake_facts_forms_then_awakens_without_skip() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_no_skip(url))


async def _assert_no_skip(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        await session.execute(
            text("UPDATE public.spirits SET bond = 50, has_been_lost = true WHERE id = :id"),
            {"id": created.spirit_id},
        )
        await _insert_memory(session, created.spirit_id, "preference")
        await _insert_memory(session, created.spirit_id, "knowledge")
        await _insert_memory(session, created.spirit_id, "sight")
        await _insert_knowledge_feed(session, owner_id=user.id, spirit_id=created.spirit_id)
        locked = await feed_repo.lock_owned_spirit(session, user.id)
        assert locked is not None
        evolved = await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.id, now=NOW)
        stage = await session.scalar(
            text("SELECT stage FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
    assert [item.to_stage for item in evolved.advances] == ["formed", "awake"]
    assert evolved.advances[0].from_stage == "whelp"
    assert evolved.advances[1].from_stage == "formed"
    assert stage == "awake"
    await engine.dispose()


def test_scholar_mark_dedup_and_room_layer() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_marks(url))


async def _assert_marks(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        locked = await feed_repo.lock_owned_spirit(session, user.id)
        assert locked is not None
        first = await grant_pact_scholar_mark(
            session,
            owner_id=user.id,
            spirit_id=locked.id,
            theme="interview",
            question_bank_version="v1",
        )
        version_after = await session.scalar(
            text("SELECT version FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
        second = await grant_pact_scholar_mark(
            session,
            owner_id=user.id,
            spirit_id=locked.id,
            theme="interview",
            question_bank_version="v1",
        )
        version_dup = await session.scalar(
            text("SELECT version FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
        marks = await session.scalar(
            text("SELECT scholar_marks FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
    assert first == ("interview-v1",)
    assert second == ("interview-v1",)
    assert int(version_dup or 0) == int(version_after or 0)
    assert list(marks or []) == ["interview-v1"]

    async with claimed_read_transaction(factory, user) as session:
        snapshot = await load_bootstrap_snapshot(session, user, now=NOW)
    assert snapshot.room is not None
    assert "scholar" in snapshot.room.layers
    assert snapshot.spirit is not None
    assert snapshot.spirit.scholar_marks == ["interview-v1"]
    await engine.dispose()
