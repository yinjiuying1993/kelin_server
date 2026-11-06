"""S3-T04: food recall returns home; feed.food does not; no memory wipe or bond drop."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.quota import FOOD_DAILY_LIMIT
from app.domain.recall import RECALL_RELATION_SUMMARY
from app.schemas.recall import RecallRequest
from app.services.quota import consume as consume_quota
from app.services.recall import recall_spirit
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
KEEP_SUMMARY = "旧雨夜还在"


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _insert_lost_spirit(
    url: str,
    *,
    user_id: uuid.UUID,
    spirit_id: uuid.UUID,
    invite: str,
    bond: int = 20,
) -> uuid.UUID:
    memory_id = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, name, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, "
                "mood, bond, status, has_been_lost, version, hatched_at, last_interact_at"
                ") VALUES ("
                ":id, :user_id, :client_id, '雾生', 'warm', :invite, "
                "65, 55, 35, 40, 40, 70, :bond, 'lost', true, 1, :hatched, :last)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": invite,
                "bond": bond,
                "hatched": NOW - timedelta(days=3),
                "last": NOW,
            },
        )
        await conn.execute(
            text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, status"
                ") VALUES (:id, :spirit_id, 'knowledge', :summary, 70, 1, 'active')"
            ),
            {"id": memory_id, "spirit_id": spirit_id, "summary": KEEP_SUMMARY},
        )
    await engine.dispose()
    return memory_id


async def _spirit_row(url: str, spirit_id: uuid.UUID) -> tuple[str, int, int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT COALESCE(a.status, s.status) AS status, s.bond, "
                    "(SELECT count(*) FROM public.memories m WHERE m.spirit_id = s.id "
                    "AND m.deleted_at IS NULL) AS memories, "
                    "(SELECT count(*) FROM public.memories m WHERE m.spirit_id = s.id "
                    "AND m.summary = :keep) AS kept "
                    "FROM public.spirits s "
                    "LEFT JOIN public.spirit_awareness a ON a.spirit_id = s.id "
                    "WHERE s.id = :id"
                ),
                {"id": spirit_id, "keep": KEEP_SUMMARY},
            )
        ).one()
    await engine.dispose()
    return str(row.status), int(row.bond), int(row.memories), int(row.kept)


def test_food_recall_returns_home_keeps_memory_and_does_not_drop_bond() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_food_recall(url))


def test_feed_food_and_quota_full_cannot_save_lost() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_feed_and_quota(url))


async def _assert_food_recall(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _insert_user(url, owner_id)
    await _insert_lost_spirit(url, user_id=owner_id, spirit_id=spirit_id, invite="T4RECAL2")
    owner = CurrentUser(id=owner_id)
    client = uuid.uuid4()
    body = RecallRequest(client_id=client, method="food")
    async with claimed_transaction(factory, owner) as session:
        first = await recall_spirit(session, owner, body, now=NOW)
    assert first.spirit.status == "home"
    assert any(item.summary == RECALL_RELATION_SUMMARY for item in first.memories)
    status, bond, memories, kept = await _spirit_row(url, spirit_id)
    assert status == "home"
    assert bond == 20
    assert memories == 2
    assert kept == 1
    async with claimed_transaction(factory, owner) as session:
        awareness = await session.scalar(
            text("SELECT status FROM public.spirit_awareness WHERE spirit_id = :id"),
            {"id": spirit_id},
        )
        replay = await recall_spirit(session, owner, body, now=NOW)
    assert str(awareness) == "home"
    assert replay.spirit.status == first.spirit.status
    try:
        async with claimed_transaction(factory, owner) as session:
            await recall_spirit(
                session, owner, RecallRequest(client_id=uuid.uuid4(), method="food"), now=NOW
            )
        raise AssertionError("expected SPIRIT_NOT_LOST")
    except ApiError as exc:
        assert exc.code == "SPIRIT_NOT_LOST"
    await engine.dispose()


async def _assert_feed_and_quota(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    quota_owner = uuid.uuid4()
    quota_spirit = uuid.uuid4()
    await _insert_user(url, quota_owner)
    await _insert_lost_spirit(url, user_id=quota_owner, spirit_id=quota_spirit, invite="T4CAPXX2")
    quota_user = CurrentUser(id=quota_owner)
    async with claimed_transaction(factory, quota_user) as session:
        for _ in range(FOOD_DAILY_LIMIT):
            await consume_quota(session, quota_owner, "food", now=NOW, timezone="Asia/Shanghai")
    try:
        async with claimed_transaction(factory, quota_user) as session:
            await recall_spirit(
                session,
                quota_user,
                RecallRequest(client_id=uuid.uuid4(), method="food"),
                now=NOW,
            )
        raise AssertionError("expected QUOTA_EXCEEDED")
    except ApiError as exc:
        assert exc.code == "QUOTA_EXCEEDED"
    status, bond, memories, kept = await _spirit_row(url, quota_spirit)
    assert status == "lost"
    assert bond == 20
    assert memories == 1
    assert kept == 1
    await engine.dispose()
