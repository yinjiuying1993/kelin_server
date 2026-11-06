"""S2-T03 facade lock, event replay, and one snapshot bump."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.central import OrchestratorRequest
from app.repositories.central.snapshot import LockedSpirit
from app.services.central import SettlementOutcome, execute
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "ABCDEFGH"
HASH_A = "a" * 64
HASH_B = "b" * 64


async def _seed(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":id, :user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_awareness (spirit_id) VALUES (:id)"
            ),
            {"id": spirit_id},
        )
        await conn.execute(
            text("INSERT INTO public.spirit_emotion (spirit_id) VALUES (:id)"),
            {"id": spirit_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_growth ("
                "spirit_id, closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES (:id, 50, 50, 50, 50, 50)"
            ),
            {"id": spirit_id},
        )
        await conn.execute(
            text("INSERT INTO public.room_projections (spirit_id) VALUES (:id)"),
            {"id": spirit_id},
        )
    await engine.dispose()


async def _versions(url: str, spirit_id: uuid.UUID) -> tuple[int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT snapshot_version, version FROM public.spirits WHERE id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return int(row[0]), int(row[1])


def _request(event_id: uuid.UUID, spirit_id: uuid.UUID, payload_hash: str) -> OrchestratorRequest:
    return OrchestratorRequest(
        request_kind="user_action",
        event_id=event_id,
        spirit_id=spirit_id,
        occurred_at=NOW,
        client_payload_hash=payload_hash,
        current_snapshot_version=1,
        action_event_type="feed.food.accepted",
    )


def test_event_replay_conflict_and_single_bump() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_facade(url))


async def _assert_facade(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    event_id = uuid.uuid4()
    await _seed(url, owner, spirit_id)
    user = CurrentUser(id=owner)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    calls = {"count": 0}

    async def settle(_session: AsyncSession, locked: LockedSpirit) -> SettlementOutcome:
        calls["count"] += 1
        return SettlementOutcome(
            changed=True,
            payload={"spirit_id": str(locked.id), "seq": calls["count"]},
        )

    async with claimed_transaction(factory, user) as session:
        first = await execute(session, user, _request(event_id, spirit_id, HASH_A), settle)
    assert first.replayed is False
    assert first.bumped is True
    assert first.snapshot_version == 2
    assert calls["count"] == 1
    snap, ver = await _versions(url, spirit_id)
    assert (snap, ver) == (2, 2)

    async with claimed_transaction(factory, user) as session:
        replayed = await execute(session, user, _request(event_id, spirit_id, HASH_A), settle)
    assert replayed.replayed is True
    assert replayed.bumped is False
    assert replayed.snapshot_version == 2
    assert replayed.payload == first.payload
    assert calls["count"] == 1
    snap, ver = await _versions(url, spirit_id)
    assert (snap, ver) == (2, 2)

    try:
        async with claimed_transaction(factory, user) as session:
            await execute(session, user, _request(event_id, spirit_id, HASH_B), settle)
    except ApiError as exc:
        assert exc.code == "IDEMPOTENCY_CONFLICT"
        assert exc.status_code == 409
    else:
        raise AssertionError("different body must conflict")
    assert calls["count"] == 1
    snap, ver = await _versions(url, spirit_id)
    assert (snap, ver) == (2, 2)

    other_event = uuid.uuid4()

    async def unchanged(_session: AsyncSession, _locked: LockedSpirit) -> SettlementOutcome:
        calls["count"] += 1
        return SettlementOutcome(changed=False, payload={"noop": True})

    async with claimed_transaction(factory, user) as session:
        skipped = await execute(
            session, user, _request(other_event, spirit_id, HASH_A), unchanged
        )
    assert skipped.replayed is False
    assert skipped.bumped is False
    assert skipped.snapshot_version == 2
    snap, ver = await _versions(url, spirit_id)
    assert (snap, ver) == (2, 2)

    await engine.dispose()


def test_missing_spirit_is_not_found() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_missing(url))


async def _assert_missing(url: str) -> None:
    user = CurrentUser(id=uuid.uuid4())
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)

    async def settle(_session: AsyncSession, locked: LockedSpirit) -> SettlementOutcome:
        raise AssertionError("settle must not run", locked)

    try:
        async with claimed_transaction(factory, user) as session:
            await execute(
                session,
                user,
                _request(uuid.uuid4(), uuid.uuid4(), HASH_A),
                settle,
            )
    except ApiError as exc:
        assert exc.code == "NOT_FOUND"
    else:
        raise AssertionError("missing spirit must 404")
    await engine.dispose()
