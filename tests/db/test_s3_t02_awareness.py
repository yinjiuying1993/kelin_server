"""S3-T02: awareness settle writes spirit_awareness only."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.services.awareness import settle_now
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "T4AWARE2"


async def _seed(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, "
                "mood, hatched_at"
                ") VALUES ("
                ":id, :user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50, "
                "70, :hatched)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
                "hatched": NOW - timedelta(days=3),
            },
        )
        await conn.execute(
            text("INSERT INTO public.spirit_awareness (spirit_id) VALUES (:id)"),
            {"id": spirit_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_emotion ("
                "spirit_id, mood, primary_label"
                ") VALUES (:id, 77, '平静')"
            ),
            {"id": spirit_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_growth ("
                "spirit_id, closeness, curiosity, sharpness, nocturnal, stubborn, bond"
                ") VALUES (:id, 50, 50, 50, 50, 50, 41)"
            ),
            {"id": spirit_id},
        )
    await engine.dispose()


async def _row(url: str, spirit_id: uuid.UUID) -> tuple[object, ...]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        awareness = (
            await conn.execute(
                text(
                    "SELECT status, hunger, energy, last_state_settled_at "
                    "FROM public.spirit_awareness WHERE spirit_id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
        others = (
            await conn.execute(
                text(
                    "SELECT s.mood, s.status, e.mood, e.primary_label, g.bond "
                    "FROM public.spirits s "
                    "JOIN public.spirit_emotion e ON e.spirit_id = s.id "
                    "JOIN public.spirit_growth g ON g.spirit_id = s.id "
                    "WHERE s.id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return tuple(awareness) + tuple(others)


def test_missing_spirit_is_noop() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_missing(url))


async def _assert_missing(url: str) -> None:
    user = CurrentUser(id=uuid.uuid4())
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        result = await settle_now(session, user, now=NOW)
    assert result.spirit_id is None
    assert result.decision is None
    assert result.changed is False
    await engine.dispose()


def test_idle_18h_writes_away_and_does_not_touch_mood_or_bond() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_away_without_emotion(url))


async def _assert_away_without_emotion(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed(url, owner, spirit_id)
    user = CurrentUser(id=owner)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text(
                "UPDATE public.spirit_awareness "
                "SET last_interact_at = :idle, last_state_settled_at = :settled "
                "WHERE spirit_id = :id"
            ),
            {
                "idle": NOW - timedelta(hours=18),
                "settled": NOW,
                "id": spirit_id,
            },
        )
        result = await settle_now(session, user, now=NOW, nocturnal=50)
    assert result.spirit_id == spirit_id
    assert result.decision is not None
    assert result.decision.status == "away"
    assert result.changed is True
    status, hunger, energy, settled, s_mood, s_status, e_mood, label, bond = await _row(
        url, spirit_id
    )
    assert status == "away"
    assert hunger == 80
    assert energy == 80
    assert settled == NOW
    assert int(s_mood) == 70
    assert s_status == "home"
    assert int(e_mood) == 77
    assert label == "平静"
    assert int(bond) == 41
    await engine.dispose()


def test_nine_hour_decay_updates_hunger_energy_and_settle_time() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_decay(url))


async def _assert_decay(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed(url, owner, spirit_id)
    user = CurrentUser(id=owner)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text(
                "UPDATE public.spirit_awareness "
                "SET last_interact_at = :interact, last_state_settled_at = :settled "
                "WHERE spirit_id = :id"
            ),
            {
                "interact": NOW - timedelta(hours=1),
                "settled": NOW - timedelta(hours=9),
                "id": spirit_id,
            },
        )
        result = await settle_now(session, user, now=NOW, nocturnal=50)
    assert result.decision is not None
    assert result.decision.status == "home"
    assert result.decision.hunger == 74
    assert result.decision.energy == 76
    status, hunger, energy, settled, s_mood, _s_status, e_mood, label, bond = await _row(
        url, spirit_id
    )
    assert status == "home"
    assert hunger == 74
    assert energy == 76
    assert settled == NOW
    assert int(s_mood) == 70
    assert int(e_mood) == 77
    assert label == "平静"
    assert int(bond) == 41
    await engine.dispose()
