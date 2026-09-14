from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from io import StringIO
from typing import Any
from unittest.mock import patch

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.logging import configure_logging
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
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
ADAPTER: TypeAdapter[FeedCreateRequest] = TypeAdapter(FeedCreateRequest)


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


def _feed(payload: dict[str, Any], *, client_id: uuid.UUID | None = None) -> Any:
    return ADAPTER.validate_python(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "kind": "sight",
            "payload": payload,
        }
    )


def _location(
    *, label: str = "外滩", city: str = "上海", category: str | None = "landmark"
) -> dict[str, Any]:
    body: dict[str, Any] = {"source": "location", "label": label, "city": city}
    if category is not None:
        body["category"] = category
    return body


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_location_sight_accepts_coarse_poi_and_rejects_privacy() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_location_sight(url))


async def _assert_location_sight(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    owner_user = CurrentUser(id=owner)
    async with claimed_transaction(factory, owner_user) as session:
        created = await create_spirit_if_absent(session, owner_user, _spirit_request())

    client_id = uuid.uuid4()
    async with claimed_transaction(factory, owner_user) as session:
        accepted = await settle_feed(
            session, owner_user, _feed(_location(), client_id=client_id), now=NOW
        )
        replay = await settle_feed(
            session, owner_user, _feed(_location(), client_id=client_id), now=NOW
        )
        uploads = await session.scalar(text("SELECT count(*) FROM public.sight_uploads"))
        payload = await session.scalar(
            text("SELECT payload::text FROM public.feeds WHERE id = :id"),
            {"id": accepted.feed.id},
        )
    assert accepted.feed.kind == "sight"
    assert accepted.feed.status == "accepted"
    assert accepted.feed.effect_applied_at is not None
    assert replay.feed.id == accepted.feed.id
    assert len(accepted.memories) == 1
    assert accepted.memories[0].type == "sight"
    assert accepted.memories[0].summary == "外滩 · 上海"
    assert accepted.memories[0].tags == ["landmark"]
    assert accepted.events[0].type == "feed.accepted"
    assert int(uploads or 0) == 0
    assert "latitude" not in str(payload)
    assert "longitude" not in str(payload)
    assert "121." not in str(payload)

    async with claimed_read_transaction(factory, owner_user) as session:
        snapshot = await load_bootstrap_snapshot(session, owner_user, now=NOW)
        quota = await session.execute(
            text(
                "SELECT used, limit_value FROM public.daily_usage "
                "WHERE user_id = :user_id AND capability = 'sight'"
            ),
            {"user_id": owner},
        )
        usage = quota.one()
        growth = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'feed_accepted'"
            ),
            {"spirit_id": created.spirit_id},
        )
    assert snapshot.room is not None
    assert snapshot.room.pending_sight is None
    assert int(usage.used) == 1
    assert int(usage.limit_value) == 2
    assert int(growth or 0) == 1

    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with claimed_transaction(factory, owner_user) as session:
            rejected = await settle_feed(
                session,
                owner_user,
                _feed(_location(label="https://maps.example.com/bund?lat=31.23")),
                now=NOW,
            )
    log_text = buf.getvalue()
    assert rejected.feed.status == "rejected"
    assert rejected.feed.rejection_code == "MODERATION_REJECTED"
    assert rejected.memories == ()
    assert rejected.events == ()
    assert "maps.example.com" not in log_text
    assert "31.23" not in log_text
    assert "location_sight_decision" in log_text
    assert "rejected" in log_text

    async with claimed_transaction(factory, owner_user) as session:
        try:
            await settle_feed(
                session, owner_user, _feed(_location(label="中山东一路12号")), now=NOW
            )
        except ApiError as exc:
            assert exc.code == "QUOTA_EXCEEDED"
            assert exc.details is not None
            assert exc.details["quota"] == "sight"
        else:
            raise AssertionError("third location sight must share the 2/day quota")
        try:
            await settle_feed(session, owner_user, _feed({"source": "photo"}), now=NOW)
        except ApiError as exc:
            assert exc.code == "QUOTA_EXCEEDED"
            assert exc.details is not None
            assert exc.details["quota"] == "sight"
        else:
            raise AssertionError("photo sight must share the 2/day quota")
        stored = await session.scalar(
            text("SELECT count(*) FROM public.feeds WHERE user_id = :user_id AND kind = 'sight'"),
            {"user_id": owner},
        )
        photo_rows = await session.scalar(
            text(
                "SELECT count(*) FROM public.feeds "
                'WHERE user_id = :user_id AND payload @> \'{"source":"photo"}\''
            ),
            {"user_id": owner},
        )
    assert int(stored or 0) == 2
    assert int(photo_rows or 0) == 0

    await engine.dispose()
