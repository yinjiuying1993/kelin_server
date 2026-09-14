from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.schemas.feed import FeedCreateRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.bootstrap import load_bootstrap_snapshot
from app.services.feed import settle_feed
from app.services.spirit import create_spirit_if_absent
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
ADAPTER: TypeAdapter[FeedCreateRequest] = TypeAdapter(FeedCreateRequest)


def _spirit_request(*, client_id: uuid.UUID | None = None) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
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


def _feed(kind: str, payload: dict[str, Any], *, client_id: uuid.UUID | None = None) -> Any:
    return ADAPTER.validate_python(
        {"client_id": str(client_id or uuid.uuid4()), "kind": kind, "payload": payload}
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _spirit_vitals(session: AsyncSession, user_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT hunger, energy, mood, version FROM public.spirits WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
    ).one()


def test_food_knowledge_emotion_quota_replay_gate_and_privacy() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_feed_settlement(url))


async def _assert_feed_settlement(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    other = uuid.uuid4()
    await _insert_auth_user(url, owner)
    await _insert_auth_user(url, other)
    owner_user = CurrentUser(id=owner)
    other_user = CurrentUser(id=other)
    async with claimed_transaction(factory, owner_user) as session:
        created = await create_spirit_if_absent(session, owner_user, _spirit_request())
    async with claimed_transaction(factory, other_user) as session:
        await create_spirit_if_absent(session, other_user, _spirit_request())

    food_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        food = await settle_feed(
            session, owner_user, _feed("food", {}, client_id=food_client), now=NOW
        )
        vitals = await _spirit_vitals(session, owner)
    assert food.spirit.hunger == 90
    assert food.spirit.energy == 90
    assert food.spirit.mood == 60
    assert food.room.weather == "cloudy"
    assert vitals.hunger == 90
    assert food.quotas[0].capability == "food"
    assert food.quotas[0].used == 1
    assert food.quotas[0].limit == 3
    assert food.feed.effect_applied_at is not None
    assert food.events[0].type == "feed.accepted"

    async with claimed_transaction(factory, owner_user) as session:
        replay = await settle_feed(
            session, owner_user, _feed("food", {}, client_id=food_client), now=NOW
        )
        vitals = await _spirit_vitals(session, owner)
        usage = await session.scalar(
            text(
                "SELECT used FROM public.daily_usage "
                "WHERE user_id = :user_id AND capability = 'food'"
            ),
            {"user_id": owner},
        )
        events = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'feed_accepted'"
            ),
            {"spirit_id": created.spirit_id},
        )
    assert replay.feed.id == food.feed.id
    assert vitals.hunger == 90
    assert int(usage or 0) == 1
    assert int(events or 0) == 1

    async with claimed_transaction(factory, owner_user) as session:
        await session.execute(
            text("UPDATE public.spirits SET hunger = 80 WHERE user_id = :user_id"),
            {"user_id": owner},
        )
        gated = await settle_feed(
            session, owner_user, _feed("food", {}, client_id=food_client), now=NOW
        )
        gated_vitals = await _spirit_vitals(session, owner)
        await session.execute(
            text("UPDATE public.spirits SET hunger = 90 WHERE user_id = :user_id"),
            {"user_id": owner},
        )
    assert gated.feed.id == food.feed.id
    assert gated.feed.effect_applied_at is not None
    assert gated_vitals.hunger == 80

    async with claimed_transaction(factory, owner_user) as session:
        try:
            await settle_feed(
                session,
                owner_user,
                _feed("knowledge", {"text": "换了正文"}, client_id=food_client),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("same client_id different payload must conflict")

    knowledge_client = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        knowledge = await settle_feed(
            session,
            owner_user,
            _feed("knowledge", {"text": "字" * 600}, client_id=knowledge_client),
            now=NOW,
        )
        memory_count = await session.scalar(
            text(
                "SELECT count(*) FROM public.memories "
                "WHERE spirit_id = :spirit_id AND source_feed_id = :feed_id"
            ),
            {"spirit_id": created.spirit_id, "feed_id": knowledge.feed.id},
        )
    assert knowledge.spirit.hunger == 90
    assert len(knowledge.memories) == 1
    assert knowledge.memories[0].summary == "字" * 500
    assert knowledge.memories[0].salience == 70
    assert knowledge.memories[0].confidence == 1.0
    assert knowledge.memories[0].tags == []
    assert int(memory_count or 0) == 1

    async with claimed_transaction(factory, owner_user) as session:
        emotion = await settle_feed(
            session,
            owner_user,
            _feed("emotion", {"emotion": "happy"}),
            now=NOW,
        )
    assert emotion.spirit.mood == 72
    assert emotion.room.weather == "clear"

    async with claimed_read_transaction(factory, owner_user) as session:
        snapshot = await load_bootstrap_snapshot(session, owner_user, now=NOW)
    assert snapshot.room is not None
    assert snapshot.room.weather == "clear"
    assert snapshot.spirit is not None
    assert snapshot.spirit.mood == 72

    async with claimed_transaction(factory, owner_user) as session:
        angry = await settle_feed(
            session,
            owner_user,
            _feed("emotion", {"emotion": "angry"}),
            now=NOW,
        )
    assert angry.spirit.mood == 57
    assert angry.room.weather == "rain"

    async with claimed_transaction(factory, owner_user) as session:
        photo = await settle_feed(session, owner_user, _feed("sight", {"source": "photo"}), now=NOW)
        sight_count = await session.scalar(
            text("SELECT count(*) FROM public.feeds WHERE user_id = :user_id AND kind = 'sight'"),
            {"user_id": owner},
        )
        uploads = await session.scalar(text("SELECT count(*) FROM public.sight_uploads"))
        growth = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'feed_accepted' "
                "AND source_id = :feed_id"
            ),
            {"spirit_id": created.spirit_id, "feed_id": photo.feed.id},
        )
    assert photo.feed.status == "pending"
    assert photo.feed.effect_applied_at is None
    assert photo.events == ()
    assert photo.memories == ()
    assert int(sight_count or 0) == 1
    assert int(uploads or 0) == 0
    assert int(growth or 0) == 0

    async with claimed_transaction(factory, owner_user) as session:
        await settle_feed(session, owner_user, _feed("food", {}), now=NOW)
        await settle_feed(session, owner_user, _feed("food", {}), now=NOW)
        third = await _spirit_vitals(session, owner)
        try:
            await settle_feed(session, owner_user, _feed("food", {}), now=NOW)
        except ApiError as exc:
            assert exc.code == "QUOTA_EXCEEDED"
            assert exc.status_code == 429
            assert exc.details is not None
            assert exc.details["quota"] == "food"
            assert "reset_at" in exc.details
        else:
            raise AssertionError("fourth food must exceed quota")
        used = await session.scalar(
            text(
                "SELECT used FROM public.daily_usage "
                "WHERE user_id = :user_id AND capability = 'food'"
            ),
            {"user_id": owner},
        )
    assert int(third.hunger) == 100
    assert int(used or 0) == 3

    async with claimed_transaction(factory, other_user) as session:
        visible = await session.scalar(
            text("SELECT count(*) FROM public.feeds WHERE user_id = :user_id"),
            {"user_id": owner},
        )
        other_food = await settle_feed(session, other_user, _feed("food", {}), now=NOW)
        other_feeds = await session.scalar(
            text("SELECT count(*) FROM public.feeds WHERE user_id = :user_id"),
            {"user_id": other},
        )
    assert int(visible or 0) == 0
    assert other_food.spirit.hunger == 90
    assert int(other_feeds or 0) == 1

    async with claimed_transaction(factory, owner_user) as session:
        owner_after = await _spirit_vitals(session, owner)
        owner_feeds = await session.scalar(
            text("SELECT count(*) FROM public.feeds WHERE user_id = :user_id"),
            {"user_id": owner},
        )
        other_from_owner = await session.scalar(
            text("SELECT count(*) FROM public.feeds WHERE user_id = :user_id"),
            {"user_id": other},
        )
    assert int(owner_after.hunger) == 100
    assert int(owner_feeds or 0) == 7
    assert int(other_from_owner or 0) == 0

    no_spirit = CurrentUser(id=uuid.uuid4())
    await _insert_auth_user(url, no_spirit.id)
    async with claimed_transaction(factory, no_spirit) as session:
        try:
            await settle_feed(session, no_spirit, _feed("food", {}), now=NOW)
        except ApiError as exc:
            assert exc.code == "NOT_FOUND"
        else:
            raise AssertionError("missing spirit must be not found")

    await engine.dispose()
