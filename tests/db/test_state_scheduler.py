from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.spirit_state import STATE_SETTLE_EVENT, state_tick_dedupe_key
from app.schemas.spirit import CreateSpiritRequest
from app.services.spirit import create_spirit_if_absent
from app.services.state_scheduler import process_due_state_ticks, scan_due_state_jobs
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _request(*, client_id: uuid.UUID | None = None) -> CreateSpiritRequest:
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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _spirit_row(session: AsyncSession, user_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT id, status, version, last_interact_at, study_until, away_until, "
                "has_been_lost FROM public.spirits WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
    ).one()


async def _set_last_interact(
    session: AsyncSession, user_id: uuid.UUID, last_interact_at: datetime
) -> None:
    await session.execute(
        text("UPDATE public.spirits SET last_interact_at = :ts WHERE user_id = :user_id"),
        {"ts": last_interact_at, "user_id": user_id},
    )


async def _outbox_ticks(url: str, spirit_id: uuid.UUID) -> list[Any]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, event_type, dedupe_key, status, owner_id "
                    "FROM public.outbox_events "
                    "WHERE aggregate_id = :spirit_id AND event_type = :event_type "
                    "ORDER BY created_at, dedupe_key"
                ),
                {"spirit_id": spirit_id, "event_type": STATE_SETTLE_EVENT},
            )
        ).all()
    await engine.dispose()
    return list(rows)


async def _growth_rows(url: str, spirit_id: uuid.UUID) -> list[Any]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT source_type, source_id, event_type, payload, applied_at "
                    "FROM public.growth_events WHERE spirit_id = :spirit_id "
                    "ORDER BY occurred_at, id"
                ),
                {"spirit_id": spirit_id},
            )
        ).all()
    await engine.dispose()
    return list(rows)


def test_under_18h_is_not_enqueued_18h_is_settled_once() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_18h_scan_and_worker(url))


async def _assert_18h_scan_and_worker(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=8, max_overflow=4)
    factory = create_session_factory(engine)

    early_user = uuid.uuid4()
    await _insert_auth_user(url, early_user)
    early = CurrentUser(id=early_user)
    async with claimed_transaction(factory, early) as session:
        early_created = await create_spirit_if_absent(session, early, _request())
        await _set_last_interact(session, early_user, NOW - timedelta(hours=17, minutes=59))

    due_user = uuid.uuid4()
    await _insert_auth_user(url, due_user)
    due = CurrentUser(id=due_user)
    last_interact = NOW - timedelta(hours=18)
    async with claimed_transaction(factory, due) as session:
        due_created = await create_spirit_if_absent(session, due, _request())
        await _set_last_interact(session, due_user, last_interact)

    scans = await asyncio.gather(*[scan_due_state_jobs(factory, now=NOW) for _ in range(8)])
    assert sum(item.inserted for item in scans) == 1
    repeat = await scan_due_state_jobs(factory, now=NOW)
    assert repeat.inserted == 0

    ticks = await _outbox_ticks(url, due_created.spirit_id)
    assert len(ticks) == 1
    assert ticks[0].status == "pending"
    assert str(ticks[0].dedupe_key) == state_tick_dedupe_key(due_created.spirit_id, now=NOW)
    assert await _outbox_ticks(url, early_created.spirit_id) == []

    processed = await process_due_state_ticks(factory, now=NOW)
    assert len(processed) == 1
    assert processed[0].spirit_id == due_created.spirit_id
    assert processed[0].changed is True
    assert processed[0].applied_time_passed is True

    async with claimed_transaction(factory, due) as session:
        row = await _spirit_row(session, due_user)
    assert row.status == "away"
    assert row.version == 2
    assert row.last_interact_at == last_interact

    growth = await _growth_rows(url, due_created.spirit_id)
    assert len(growth) == 1
    assert growth[0].event_type == "time_passed"
    assert growth[0].source_type == "scheduler"
    assert str(growth[0].source_id) == str(processed[0].job_id)
    assert growth[0].applied_at is not None

    after = await _outbox_ticks(url, due_created.spirit_id)
    settle_rows = [item for item in after if ":home:away:2" in str(item.dedupe_key)]
    tick_rows = [item for item in after if str(item.dedupe_key).endswith(":2026090912")]
    assert len(settle_rows) == 1
    assert len(tick_rows) == 1
    assert tick_rows[0].status == "done"

    again = await process_due_state_ticks(factory, now=NOW)
    assert again == ()
    async with claimed_transaction(factory, due) as session:
        stable = await _spirit_row(session, due_user)
    assert stable.version == 2
    assert stable.status == "away"
    assert len(await _growth_rows(url, due_created.spirit_id)) == 1

    next_hour = NOW + timedelta(hours=1)
    later_scan = await scan_due_state_jobs(factory, now=next_hour)
    assert later_scan.inserted == 2
    later = await process_due_state_ticks(factory, now=next_hour)
    assert {item.spirit_id for item in later} == {due_created.spirit_id, early_created.spirit_id}
    due_later = next(item for item in later if item.spirit_id == due_created.spirit_id)
    early_later = next(item for item in later if item.spirit_id == early_created.spirit_id)
    assert due_later.changed is False
    assert due_later.applied_time_passed is True
    assert early_later.changed is True
    assert early_later.applied_time_passed is True
    async with claimed_transaction(factory, due) as session:
        still = await _spirit_row(session, due_user)
    assert still.version == 2
    assert still.status == "away"
    assert still.last_interact_at == last_interact
    assert len(await _growth_rows(url, due_created.spirit_id)) == 2
    await engine.dispose()


def test_study_until_expiry_is_enqueued_and_returns_home() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_study_until_tick(url))


async def _assert_study_until_tick(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    last_interact = NOW - timedelta(hours=1)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _request())
        await session.execute(
            text(
                "UPDATE public.spirits SET status = 'study', "
                "study_until = :until, last_interact_at = :last "
                "WHERE user_id = :user_id"
            ),
            {"until": NOW, "last": last_interact, "user_id": user_id},
        )

    scanned = await scan_due_state_jobs(factory, now=NOW)
    assert scanned.inserted == 1
    processed = await process_due_state_ticks(factory, now=NOW)
    assert len(processed) == 1
    assert processed[0].changed is True
    async with claimed_transaction(factory, user) as session:
        row = await _spirit_row(session, user_id)
    assert row.status == "home"
    assert row.study_until is None
    assert row.version == 2
    assert row.last_interact_at == last_interact
    assert created.spirit_id == processed[0].spirit_id
    await engine.dispose()
