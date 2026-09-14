"""P20 POST /recall: lost-only, source checks, free food, concurrency, replay."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.feed import FOOD_DAILY_LIMIT
from app.domain.recall import RECALL_RELATION_SUMMARY, recall_growth_payload
from app.schemas.recall import RecallFoodRequest, RecallSightRequest
from app.services.quota import consume as consume_quota
from app.services.recall import recall_spirit
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
INVITE = "SETAB234"


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _insert_spirit(
    url: str,
    *,
    user_id: uuid.UUID,
    spirit_id: uuid.UUID,
    name: str = "雾生",
    invite: str = INVITE,
    status: str = "lost",
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, name, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, "
                "status, has_been_lost, version"
                ") VALUES ("
                ":id, :user_id, :client_id, :name, 'warm', :invite, "
                "65, 55, 35, 40, 40, :status, true, 1)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "name": name,
                "invite": invite,
                "status": status,
            },
        )
        await conn.execute(
            text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
    await engine.dispose()


async def _insert_memory(
    url: str,
    *,
    memory_id: uuid.UUID,
    spirit_id: uuid.UUID,
    memory_type: str = "sight",
    status: str = "active",
) -> None:
    sealed = "now()" if status == "sealed" else "NULL"
    deleted = "now()" if status == "deleted" else "NULL"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, status, "
                "sealed_at, deleted_at"
                f") VALUES (:id, :spirit_id, :type, '旧见闻', 70, 1, :status, {sealed}, {deleted})"
            ),
            {
                "id": memory_id,
                "spirit_id": spirit_id,
                "type": memory_type,
                "status": status,
            },
        )
    await engine.dispose()


def test_food_recall_home_relation_memory_replay_and_not_lost() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_food_and_replay(url))


def test_sight_source_matrix_quota_full_and_concurrent() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_sight_quota_concurrent(url))


async def _assert_food_and_replay(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _insert_user(url, owner_id)
    await _insert_spirit(url, user_id=owner_id, spirit_id=spirit_id)
    owner = CurrentUser(id=owner_id)
    client = uuid.uuid4()
    body = RecallFoodRequest(client_id=client, method="food")
    async with claimed_transaction(factory, owner) as session:
        first = await recall_spirit(session, owner, body, now=NOW)
    assert first.spirit.status == "home"
    assert first.spirit.hunger == 80
    assert first.spirit.bond == 5
    assert first.spirit.mood == 68
    assert first.spirit.closeness == 67
    assert first.snapshot_version >= 2
    assert any(item.summary == RECALL_RELATION_SUMMARY for item in first.memories)
    assert any(event.type == "recall.returned" for event in first.events)
    food_quota = next(item for item in first.quotas if item.capability == "food")
    assert food_quota.used == 1
    async with claimed_transaction(factory, owner) as session:
        replay = await recall_spirit(session, owner, body, now=NOW)
    assert replay.spirit.version == first.spirit.version
    assert replay.spirit.bond == first.spirit.bond
    assert len(replay.memories) == 1
    async with claimed_transaction(factory, owner) as session:
        count = await session.scalar(
            text(
                "SELECT count(*) FROM public.memories "
                "WHERE spirit_id = :spirit_id AND summary = :summary"
            ),
            {"spirit_id": spirit_id, "summary": RECALL_RELATION_SUMMARY},
        )
        growth = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE source_type = 'recall' AND source_id = :source_id "
                "AND event_type = 'returned_from_lost'"
            ),
            {"source_id": client},
        )
    assert int(count) == 1
    assert int(growth) == 1
    home_body = RecallFoodRequest(client_id=uuid.uuid4(), method="food")
    try:
        async with claimed_transaction(factory, owner) as session:
            await recall_spirit(session, owner, home_body, now=NOW)
        raise AssertionError("expected SPIRIT_NOT_LOST")
    except ApiError as exc:
        assert exc.code == "SPIRIT_NOT_LOST"
    await engine.dispose()


async def _assert_sight_quota_concurrent(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    spirit_a = uuid.uuid4()
    spirit_b = uuid.uuid4()
    await _insert_user(url, owner_a)
    await _insert_user(url, owner_b)
    await _insert_spirit(url, user_id=owner_a, spirit_id=spirit_a, invite="SETAA234")
    await _insert_spirit(url, user_id=owner_b, spirit_id=spirit_b, name="乙灵", invite="SETBB234")
    active = uuid.uuid4()
    sealed = uuid.uuid4()
    deleted = uuid.uuid4()
    foreign = uuid.uuid4()
    knowledge = uuid.uuid4()
    await _insert_memory(url, memory_id=active, spirit_id=spirit_a)
    await _insert_memory(url, memory_id=sealed, spirit_id=spirit_a, status="sealed")
    await _insert_memory(url, memory_id=deleted, spirit_id=spirit_a, status="deleted")
    await _insert_memory(url, memory_id=foreign, spirit_id=spirit_b)
    await _insert_memory(url, memory_id=knowledge, spirit_id=spirit_a, memory_type="knowledge")
    user_a = CurrentUser(id=owner_a)
    for memory_id in (sealed, deleted, foreign, knowledge):
        body = RecallSightRequest(
            client_id=uuid.uuid4(), method="sight_memory", memory_id=memory_id
        )
        try:
            async with claimed_transaction(factory, user_a) as session:
                await recall_spirit(session, user_a, body, now=NOW)
            raise AssertionError(f"expected RECALL_SOURCE_INVALID for {memory_id}")
        except ApiError as exc:
            assert exc.code == "RECALL_SOURCE_INVALID"
    async with claimed_transaction(factory, user_a) as session:
        status = await session.scalar(
            text("SELECT status FROM public.spirits WHERE id = :id"),
            {"id": spirit_a},
        )
    assert status == "lost"
    ok_body = RecallSightRequest(client_id=uuid.uuid4(), method="sight_memory", memory_id=active)
    async with claimed_transaction(factory, user_a) as session:
        sight = await recall_spirit(session, user_a, ok_body, now=NOW)
    assert sight.spirit.status == "home"
    assert sight.spirit.hunger == 80
    assert any(item.summary == RECALL_RELATION_SUMMARY for item in sight.memories)

    owner_c = uuid.uuid4()
    spirit_c = uuid.uuid4()
    await _insert_user(url, owner_c)
    await _insert_spirit(url, user_id=owner_c, spirit_id=spirit_c, invite="SETCC234")
    user_c = CurrentUser(id=owner_c)
    async with claimed_transaction(factory, user_c) as session:
        for _ in range(FOOD_DAILY_LIMIT):
            await consume_quota(session, owner_c, "food", now=NOW, timezone="Asia/Shanghai")
    full_body = RecallFoodRequest(client_id=uuid.uuid4(), method="food")
    async with claimed_transaction(factory, user_c) as session:
        free = await recall_spirit(session, user_c, full_body, now=NOW)
    assert free.spirit.status == "home"
    assert free.spirit.hunger == 80
    assert free.spirit.energy == 80
    food_quota = next(item for item in free.quotas if item.capability == "food")
    assert food_quota.used == food_quota.limit == FOOD_DAILY_LIMIT

    owner_d = uuid.uuid4()
    spirit_d = uuid.uuid4()
    await _insert_user(url, owner_d)
    await _insert_spirit(url, user_id=owner_d, spirit_id=spirit_d, invite="SETDD234")
    sight_id = uuid.uuid4()
    await _insert_memory(url, memory_id=sight_id, spirit_id=spirit_d)
    user_d = CurrentUser(id=owner_d)
    food_body = RecallFoodRequest(client_id=uuid.uuid4(), method="food")
    sight_body = RecallSightRequest(
        client_id=uuid.uuid4(), method="sight_memory", memory_id=sight_id
    )

    async def _run(body: RecallFoodRequest | RecallSightRequest) -> str:
        try:
            async with claimed_transaction(factory, user_d) as session:
                await recall_spirit(session, user_d, body, now=NOW)
            return "ok"
        except ApiError as exc:
            return exc.code

    outcomes = await asyncio.gather(_run(food_body), _run(sight_body))
    assert outcomes.count("ok") == 1
    assert outcomes.count("SPIRIT_NOT_LOST") == 1
    payload = recall_growth_payload()
    assert "hunger_delta" not in payload
    assert "energy_delta" not in payload
    await engine.dispose()
