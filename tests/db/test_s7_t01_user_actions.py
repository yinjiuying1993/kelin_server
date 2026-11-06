"""S7-T01: a rejected feed does not change hunger, bond, or mood."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.services.user_actions import record_user_action
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
DAY = date(2026, 9, 20)
INVITE = "T7ACT234"


async def _seed(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, "
                "hunger, mood, bond, status"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, "
                "50, 50, 50, 50, 50, 40, 60, 10, 'home')"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
            },
        )
    await engine.dispose()


async def _numbers(url: str, spirit_id: uuid.UUID) -> tuple[int, int, int, str, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT hunger, mood, bond, status, "
                    "COALESCE((SELECT used FROM public.action_quotas "
                    "WHERE spirit_id = :id AND capability = 'food'), 0) "
                    "FROM public.spirits WHERE id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return int(row[0]), int(row[1]), int(row[2]), str(row[3]), int(row[4])


def test_rejected_food_leaves_spirit_numbers_unchanged() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_food(url))


async def _feed(session, user, spirit_id, event_id, payload: dict[str, object]):
    return await record_user_action(
        session,
        user,
        spirit_id=spirit_id,
        action_type="food",
        event_id=event_id,
        payload=payload,
        status="home",
        active_promise=False,
        knowledge_moderated=False,
        pact_accepted=False,
        usage_date=DAY,
        timezone="Asia/Shanghai",
        now=NOW,
    )


async def _assert_food(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed(url, user_id, spirit_id)
    user = CurrentUser(id=user_id)
    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    first = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        for index in range(3):
            event_id = first if index == 0 else uuid.uuid4()
            accepted = await _feed(session, user, spirit_id, event_id, {})
            assert accepted.accepted is True
            assert accepted.event_type == "feed.food.accepted"
    assert await _numbers(url, spirit_id) == (40, 60, 10, "home", 3)
    async with claimed_transaction(factory, user) as session:
        replay = await _feed(session, user, spirit_id, first, {})
        fourth = await _feed(session, user, spirit_id, uuid.uuid4(), {})
        blocked = await _feed(session, user, spirit_id, uuid.uuid4(), {"bond_delta": 4})
    assert replay.replayed is True
    assert replay.quota_cost == 0
    assert fourth.accepted is False
    assert fourth.rejection_code == "QUOTA_EXCEEDED"
    assert blocked.rejection_code == "INVALID_INPUT"
    assert await _numbers(url, spirit_id) == (40, 60, 10, "home", 3)
    await runtime.dispose()
