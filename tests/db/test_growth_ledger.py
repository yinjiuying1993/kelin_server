from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.repositories import feed as feed_repo
from app.schemas.spirit import CreateSpiritRequest
from app.services.growth import record_and_apply
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


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


def test_concurrent_same_source_applies_once() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_concurrent_once(url))


async def _assert_concurrent_once(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=8, max_overflow=8)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    source_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        hunger_before = await session.scalar(
            text("SELECT hunger FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )

    async def _one() -> None:
        async with claimed_transaction(factory, user) as session:
            locked = await feed_repo.lock_owned_spirit(session, user.id)
            assert locked is not None
            await record_and_apply(
                session,
                owner_id=user.id,
                spirit_id=locked.id,
                event_type="feed_accepted",
                source_id=source_id,
                payload={"hunger_delta": 10},
                now=NOW,
                touch_interact=True,
            )

    await asyncio.gather(*[_one() for _ in range(8)])
    async with claimed_transaction(factory, user) as session:
        row = (
            await session.execute(
                text(
                    "SELECT hunger, "
                    "(SELECT count(*) FROM public.growth_events "
                    "WHERE source_id = :source_id) AS events, "
                    "(SELECT payload FROM public.growth_events "
                    "WHERE source_id = :source_id) AS payload, "
                    "(SELECT applied_at IS NOT NULL FROM public.growth_events "
                    "WHERE source_id = :source_id) AS applied "
                    "FROM public.spirits WHERE id = :id"
                ),
                {"id": created.spirit_id, "source_id": source_id},
            )
        ).one()
    assert int(hunger_before or 0) == 80
    assert int(row.hunger) == 90
    assert int(row.events) == 1
    assert row.payload == {"hunger_delta": 10}
    assert bool(row.applied) is True
    await engine.dispose()


def test_upper_bound_clamps_and_stores_intent_delta() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_clamp_payload(url))


async def _assert_clamp_payload(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    source_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        await session.execute(
            text("UPDATE public.spirits SET hunger = 95 WHERE id = :id"),
            {"id": created.spirit_id},
        )
        locked = await feed_repo.lock_owned_spirit(session, user.id)
        assert locked is not None
        await record_and_apply(
            session,
            owner_id=user.id,
            spirit_id=locked.id,
            event_type="feed_accepted",
            source_id=source_id,
            payload={"hunger_delta": 10},
            now=NOW,
            touch_interact=True,
        )
        hunger = await session.scalar(
            text("SELECT hunger FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
        payload = await session.scalar(
            text("SELECT payload FROM public.growth_events WHERE source_id = :id"),
            {"id": source_id},
        )
    assert int(hunger or 0) == 100
    assert payload == {"hunger_delta": 10}
    await engine.dispose()


def test_rollback_leaves_no_growth_row() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_rollback(url))


async def _assert_rollback(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    source_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        hunger_before = await session.scalar(
            text("SELECT hunger FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )

    try:
        async with claimed_transaction(factory, user) as session:
            locked = await feed_repo.lock_owned_spirit(session, user.id)
            assert locked is not None
            await record_and_apply(
                session,
                owner_id=user.id,
                spirit_id=locked.id,
                event_type="feed_accepted",
                source_id=source_id,
                payload={"hunger_delta": 10},
                now=NOW,
                touch_interact=True,
            )
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass

    async with claimed_transaction(factory, user) as session:
        hunger = await session.scalar(
            text("SELECT hunger FROM public.spirits WHERE id = :id"),
            {"id": created.spirit_id},
        )
        events = await session.scalar(
            text("SELECT count(*) FROM public.growth_events WHERE source_id = :id"),
            {"id": source_id},
        )
    assert int(hunger or 0) == int(hunger_before or 0)
    assert int(events or 0) == 0
    await engine.dispose()
