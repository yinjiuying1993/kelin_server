"""P20 POST /recall: lost-only food, replay, quota, concurrency."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.quota import FOOD_DAILY_LIMIT
from app.domain.recall import RECALL_RELATION_SUMMARY, recall_growth_payload
from app.schemas.recall import RecallRequest
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
                "status, has_been_lost, version, hatched_at, last_interact_at"
                ") VALUES ("
                ":id, :user_id, :client_id, :name, 'warm', :invite, "
                "65, 55, 35, 40, 40, :status, true, 1, :hatched, :last)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "name": name,
                "invite": invite,
                "status": status,
                "hatched": NOW - timedelta(days=3),
                "last": NOW,
            },
        )
        await conn.execute(
            text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
    await engine.dispose()


def test_food_recall_home_relation_memory_replay_and_not_lost() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_food_and_replay(url))


def test_quota_full_fails_and_concurrent_food_settles_once() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_quota_and_concurrent(url))


async def _assert_food_and_replay(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _insert_user(url, owner_id)
    await _insert_spirit(url, user_id=owner_id, spirit_id=spirit_id)
    owner = CurrentUser(id=owner_id)
    client = uuid.uuid4()
    body = RecallRequest(client_id=client, method="food")
    async with claimed_transaction(factory, owner) as session:
        first = await recall_spirit(session, owner, body, now=NOW)
    assert first.spirit.status == "home"
    assert first.snapshot_version == 2
    assert any(event.type == "recall.returned" for event in first.events)
    assert [item.summary for item in first.memories] == [RECALL_RELATION_SUMMARY]
    food_quota = next(item for item in first.quotas if item.capability == "food")
    assert food_quota.used == 1
    async with claimed_transaction(factory, owner) as session:
        replay = await recall_spirit(session, owner, body, now=NOW)
    assert replay.spirit.version == first.spirit.version
    assert [item.summary for item in replay.memories] == [RECALL_RELATION_SUMMARY]
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
    assert int(growth) == 0
    home_body = RecallRequest(client_id=uuid.uuid4(), method="food")
    try:
        async with claimed_transaction(factory, owner) as session:
            await recall_spirit(session, owner, home_body, now=NOW)
        raise AssertionError("expected SPIRIT_NOT_LOST")
    except ApiError as exc:
        assert exc.code == "SPIRIT_NOT_LOST"
    await engine.dispose()


async def _assert_quota_and_concurrent(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=8, max_overflow=4)
    factory = create_session_factory(engine)
    owner_c = uuid.uuid4()
    spirit_c = uuid.uuid4()
    await _insert_user(url, owner_c)
    await _insert_spirit(url, user_id=owner_c, spirit_id=spirit_c, invite="SETCC234")
    user_c = CurrentUser(id=owner_c)
    async with claimed_transaction(factory, user_c) as session:
        for _ in range(FOOD_DAILY_LIMIT):
            await consume_quota(session, owner_c, "food", now=NOW, timezone="Asia/Shanghai")
    full_body = RecallRequest(client_id=uuid.uuid4(), method="food")
    try:
        async with claimed_transaction(factory, user_c) as session:
            await recall_spirit(session, user_c, full_body, now=NOW)
        raise AssertionError("expected QUOTA_EXCEEDED")
    except ApiError as exc:
        assert exc.code == "QUOTA_EXCEEDED"

    owner_d = uuid.uuid4()
    spirit_d = uuid.uuid4()
    await _insert_user(url, owner_d)
    await _insert_spirit(url, user_id=owner_d, spirit_id=spirit_d, invite="SETDD234")
    user_d = CurrentUser(id=owner_d)
    food_a = RecallRequest(client_id=uuid.uuid4(), method="food")
    food_b = RecallRequest(client_id=uuid.uuid4(), method="food")

    async def _run(body: RecallRequest) -> str:
        try:
            async with claimed_transaction(factory, user_d) as session:
                await recall_spirit(session, user_d, body, now=NOW)
            return "ok"
        except ApiError as exc:
            return exc.code

    outcomes = await asyncio.gather(_run(food_a), _run(food_b))
    assert outcomes.count("ok") == 1
    assert outcomes.count("SPIRIT_NOT_LOST") == 1
    payload = recall_growth_payload()
    assert "hunger_delta" not in payload
    assert "energy_delta" not in payload
    await engine.dispose()
