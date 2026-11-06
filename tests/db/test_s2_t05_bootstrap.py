"""S2-T05: bootstrap and later writes share snapshot_version; time change may bump."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.domain.central import OrchestratorRequest
from app.repositories.central.snapshot import LockedSpirit
from app.services.bootstrap import load_bootstrap_snapshot, run_bootstrap_snapshot
from app.services.central import SettlementOutcome, execute
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "T4PYPE2X"
HASH_A = "a" * 64


async def _seed(
    url: str,
    user_id: uuid.UUID,
    spirit_id: uuid.UUID,
    *,
    last_interact_at: datetime,
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, "
                "last_interact_at, hatched_at"
                ") VALUES ("
                ":id, :user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50, "
                ":last_interact_at, :hatched_at)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
                "last_interact_at": last_interact_at,
                "hatched_at": last_interact_at,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_awareness ("
                "spirit_id, last_interact_at, last_state_settled_at"
                ") VALUES (:id, :last, :last)"
            ),
            {"id": spirit_id, "last": last_interact_at},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_emotion (spirit_id, mood, primary_label) "
                "VALUES (:id, 60, 'calm')"
            ),
            {"id": spirit_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_growth ("
                "spirit_id, closeness, curiosity, sharpness, nocturnal, stubborn, bond"
                ") VALUES (:id, 50, 50, 50, 50, 50, 0)"
            ),
            {"id": spirit_id},
        )
    await engine.dispose()


async def _projection(url: str, spirit_id: uuid.UUID) -> object:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT stage_image_key, primary_emotion_label, letter_key, "
                    "text_status, status, snapshot_version "
                    "FROM public.room_projections WHERE spirit_id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return row


async def _versions(url: str, spirit_id: uuid.UUID) -> tuple[int, int, str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT snapshot_version, version, status "
                    "FROM public.spirits WHERE id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return int(row[0]), int(row[1]), str(row[2])


async def _idempotency_count(url: str, user_id: uuid.UUID) -> int:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT COUNT(*) FROM public.idempotency_records WHERE user_id = :id"),
            {"id": user_id},
        )
    await engine.dispose()
    return int(value or 0)


def test_new_user_bootstrap_skips_facade_lock() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_empty(url))


async def _assert_empty(url: str) -> None:
    owner = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": owner})
    await engine.dispose()
    user = CurrentUser(id=owner)
    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    async with claimed_transaction(factory, user) as session:
        snapshot = await run_bootstrap_snapshot(session, user, now=NOW)
    assert snapshot.spirit is None
    assert snapshot.room is None
    assert snapshot.snapshot_version == 0
    await runtime.dispose()


def test_unchanged_bootstrap_does_not_bump_and_shares_version_with_write() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_stable_then_write(url))


async def _assert_stable_then_write(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed(url, owner, spirit_id, last_interact_at=NOW - timedelta(hours=1))
    user = CurrentUser(id=owner)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        first = await run_bootstrap_snapshot(session, user, now=NOW)
    snap, ver, status = await _versions(url, spirit_id)
    assert first.spirit is not None
    assert first.room is not None
    assert first.room.status == "home"
    assert first.snapshot_version == snap == ver == 1
    assert first.spirit.version == 1
    assert status == "home"
    assert await _idempotency_count(url, owner) == 0

    async def write_settle(_session: AsyncSession, locked: LockedSpirit) -> SettlementOutcome:
        return SettlementOutcome(changed=True, payload={"spirit_id": str(locked.id)})

    async with claimed_transaction(factory, user) as session:
        written = await execute(
            session,
            user,
            OrchestratorRequest(
                request_kind="user_action",
                event_id=uuid.uuid4(),
                spirit_id=spirit_id,
                occurred_at=NOW,
                client_payload_hash=HASH_A,
                current_snapshot_version=snap,
                action_event_type="feed.food.accepted",
            ),
            write_settle,
        )
    assert written.snapshot_version == 2
    snap, ver, status = await _versions(url, spirit_id)
    assert (snap, ver, status) == (2, 2, "home")

    async with claimed_transaction(factory, user) as session:
        again = await run_bootstrap_snapshot(session, user, now=NOW)
    assert again.snapshot_version == 2
    assert again.snapshot_version == snap
    await engine.dispose()


def test_idle_time_change_bumps_snapshot_version() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_time_bump(url))


async def _assert_time_bump(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed(url, owner, spirit_id, last_interact_at=NOW - timedelta(hours=18))
    user = CurrentUser(id=owner)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        snapshot = await run_bootstrap_snapshot(session, user, now=NOW)
    snap, ver, status = await _versions(url, spirit_id)
    assert snapshot.room is not None
    assert snapshot.room.status == "away"
    assert snapshot.snapshot_version == snap == ver == 2
    assert status == "home"
    assert snapshot.spirit is not None
    assert snapshot.spirit.stage == "formed"
    stored = await _projection(url, spirit_id)
    assert stored.stage_image_key == snapshot.room.stage_image_key
    assert stored.primary_emotion_label == "平静"
    assert stored.letter_key == snapshot.room.letter_key
    assert stored.text_status == snapshot.room.text_status
    assert stored.status == "away"
    assert int(stored.snapshot_version) == 2

    async with claimed_transaction(factory, user) as session:
        replay = await run_bootstrap_snapshot(session, user, now=NOW)
    snap2, ver2, status2 = await _versions(url, spirit_id)
    assert replay.snapshot_version == 2
    assert (snap2, ver2, status2) == (2, 2, "home")
    replayed = await _projection(url, spirit_id)
    assert replayed.status == "away"
    assert int(replayed.snapshot_version) == 2
    await engine.dispose()


def test_bootstrap_reads_split_tables_before_wide_row() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_split_table_read(url))


async def _assert_split_table_read(url: str) -> None:
    split_owner = uuid.uuid4()
    split_spirit = uuid.uuid4()
    wide_owner = uuid.uuid4()
    wide_spirit = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (:id), (:wide)"),
            {"id": split_owner, "wide": wide_owner},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, "
                "hunger, energy, mood, bond, stage, status, "
                "last_interact_at, hatched_at"
                ") VALUES ("
                ":id, :user_id, :client_id, 'wild', :invite, "
                "10, 10, 10, 10, 10, 11, 22, 33, 4, 'whelp', 'home', "
                ":last_interact_at, :hatched_at)"
            ),
            {
                "id": split_spirit,
                "user_id": split_owner,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
                "last_interact_at": NOW,
                "hatched_at": NOW,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_awareness ("
                "spirit_id, status, hunger, energy, last_interact_at, last_state_settled_at"
                ") VALUES (:id, 'study', 77, 66, :last, :last)"
            ),
            {"id": split_spirit, "last": NOW},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_emotion (spirit_id, mood, primary_label) "
                "VALUES (:id, 55, 'focused')"
            ),
            {"id": split_spirit},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_growth ("
                "spirit_id, closeness, curiosity, sharpness, nocturnal, stubborn, "
                "bond, stage, stage_image_key"
                ") VALUES (:id, 70, 71, 72, 73, 74, 9, 'formed', 'formed_image')"
            ),
            {"id": split_spirit},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, "
                "hunger, last_interact_at, hatched_at"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', 'K7M2Q9R4', "
                "50, 50, 50, 50, 50, 15, :last_interact_at, :hatched_at)"
            ),
            {
                "id": wide_spirit,
                "user_id": wide_owner,
                "client_id": uuid.uuid4(),
                "last_interact_at": NOW,
                "hatched_at": NOW,
            },
        )
    await engine.dispose()

    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    split_user = CurrentUser(id=split_owner)
    async with claimed_read_transaction(factory, split_user) as session:
        split = await load_bootstrap_snapshot(session, split_user, now=NOW)
    assert split.spirit is not None
    assert split.room is not None
    assert split.spirit.stage == "formed"
    assert split.spirit.status == "away"
    assert split.snapshot_version == 1
    assert split.room.status == "away"
    assert split.room.stage_image_key == "formed_image"
    assert set(split.room.model_dump()) == {
        "stage_image_key",
        "letter_key",
        "text_status",
        "status",
    }
    assert "scholar_marks" not in split.spirit.model_dump()

    wide_user = CurrentUser(id=wide_owner)
    async with claimed_read_transaction(factory, wide_user) as session:
        wide = await load_bootstrap_snapshot(session, wide_user, now=NOW)
    assert wide.spirit is not None
    assert wide.room is not None
    assert wide.spirit.status == "home"
    assert wide.spirit.stage == "formed"
    assert wide.room.stage_image_key == "formed_image"
    await runtime.dispose()
