from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.spirit_state import STATE_SETTLE_EVENT
from app.schemas.social import parse_outbox_payload
from app.schemas.spirit import CreateSpiritRequest
from app.services.spirit import create_spirit_if_absent
from app.services.spirit_state import settle_spirit_state
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


async def _outbox_rows(url: str, owner_id: uuid.UUID) -> list[Any]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT event_type, dedupe_key, payload, owner_id, aggregate_type "
                    "FROM public.outbox_events WHERE owner_id = :owner_id "
                    "ORDER BY created_at, dedupe_key"
                ),
                {"owner_id": owner_id},
            )
        ).all()
    await engine.dispose()
    return list(rows)


async def _visit_count(url: str, spirit_id: uuid.UUID) -> int:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT count(*) FROM public.visits WHERE visitor_spirit_id = :id"),
            {"id": spirit_id},
        )
    await engine.dispose()
    return int(count or 0)


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


def test_settle_is_noop_without_spirit_and_rejects_readonly() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_noop_and_readonly(url))


async def _assert_noop_and_readonly(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user = CurrentUser(id=uuid.uuid4())
    await _insert_auth_user(url, user.id)
    async with claimed_transaction(factory, user) as session:
        result = await settle_spirit_state(session, user, now=NOW)
    assert result.changed is False
    assert result.spirit_id is None
    assert await _outbox_rows(url, user.id) == []

    async with factory() as session:
        async with session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            with pytest.raises(RuntimeError, match="read-only"):
                await settle_spirit_state(session, user, now=NOW)
    await engine.dispose()


def test_recent_spirit_does_not_bump_or_write_outbox() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_recent_unchanged(url))


async def _assert_recent_unchanged(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _request())
        await _set_last_interact(session, user_id, NOW - timedelta(hours=1))
        first = await settle_spirit_state(session, user, now=NOW)
        row = await _spirit_row(session, user_id)
    assert first.changed is False
    assert first.version == 1
    assert first.status == "home"
    assert first.last_interact_at == NOW - timedelta(hours=1)
    assert row.status == "home"
    assert row.version == 1
    assert row.last_interact_at == NOW - timedelta(hours=1)
    assert created.spirit_id == first.spirit_id
    assert await _outbox_rows(url, user_id) == []
    await engine.dispose()


def test_72h_lost_bumps_once_and_repeat_is_stable() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_lost_idempotent(url))


async def _assert_lost_idempotent(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    last_interact = NOW - timedelta(hours=72)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _request())
        await _set_last_interact(session, user_id, last_interact)
        first = await settle_spirit_state(session, user, now=NOW)
        row = await _spirit_row(session, user_id)
    assert first.changed is True
    assert first.status == "lost"
    assert first.version == 2
    assert row.has_been_lost is True
    assert row.study_until is None
    assert row.away_until is None
    assert row.last_interact_at == last_interact
    async with claimed_transaction(factory, user) as session:
        second = await settle_spirit_state(session, user, now=NOW)
        row = await _spirit_row(session, user_id)
    assert second.changed is False
    assert second.version == 2
    assert row.status == "lost"
    assert row.last_interact_at == last_interact
    outbox = await _outbox_rows(url, user_id)
    assert len(outbox) == 1
    assert outbox[0].event_type == STATE_SETTLE_EVENT
    assert outbox[0].aggregate_type == "spirit"
    assert str(outbox[0].owner_id) == str(user_id)
    payload = parse_outbox_payload(outbox[0].payload)
    assert payload.resource_id == created.spirit_id
    assert ":home:lost:2" in str(outbox[0].dedupe_key)
    assert await _visit_count(url, created.spirit_id) == 0
    await engine.dispose()


def test_18h_away_or_study_and_until_expiry() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_18h_and_until(url))


async def _assert_18h_and_until(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)

    away_user = uuid.uuid4()
    await _insert_auth_user(url, away_user)
    away = CurrentUser(id=away_user)
    async with claimed_transaction(factory, away) as session:
        created_away = await create_spirit_if_absent(session, away, _request())
        await _set_last_interact(session, away_user, NOW - timedelta(hours=18))
        result = await settle_spirit_state(session, away, now=NOW)
        row = await _spirit_row(session, away_user)
    assert result.changed is True
    assert result.status == "away"
    assert result.version == 2
    assert row.status == "away"
    assert row.last_interact_at == NOW - timedelta(hours=18)
    async with claimed_transaction(factory, away) as session:
        repeat = await settle_spirit_state(session, away, now=NOW)
    assert repeat.changed is False
    assert repeat.version == 2
    assert await _visit_count(url, created_away.spirit_id) == 0

    visit_off_user = uuid.uuid4()
    await _insert_auth_user(url, visit_off_user)
    visit_off = CurrentUser(id=visit_off_user)
    async with claimed_transaction(factory, visit_off) as session:
        await create_spirit_if_absent(session, visit_off, _request())
        await session.execute(
            text("UPDATE public.user_preferences SET visit_on = false WHERE user_id = :id"),
            {"id": visit_off_user},
        )
        await _set_last_interact(session, visit_off_user, NOW - timedelta(hours=18))
        result = await settle_spirit_state(session, visit_off, now=NOW)
        row = await _spirit_row(session, visit_off_user)
    assert result.status == "study"
    assert row.status == "study"

    pact_user = uuid.uuid4()
    await _insert_auth_user(url, pact_user)
    pact_owner = CurrentUser(id=pact_user)
    async with claimed_transaction(factory, pact_owner) as session:
        created_pact = await create_spirit_if_absent(session, pact_owner, _request())
        await session.execute(
            text(
                "INSERT INTO public.pacts ("
                "spirit_id, client_id, theme, title, question_bank_version, "
                "week_start, starts_at, ends_at"
                ") VALUES ("
                ":spirit_id, :client_id, 'interview', '面试周', 'bank-v1', "
                "DATE '2026-09-08', :starts_at, :ends_at)"
            ),
            {
                "spirit_id": created_pact.spirit_id,
                "client_id": uuid.uuid4(),
                "starts_at": NOW - timedelta(days=1),
                "ends_at": NOW + timedelta(days=6),
            },
        )
        await _set_last_interact(session, pact_user, NOW - timedelta(hours=18))
        result = await settle_spirit_state(session, pact_owner, now=NOW)
        row = await _spirit_row(session, pact_user)
    assert result.status == "study"
    assert row.status == "study"

    until_user = uuid.uuid4()
    await _insert_auth_user(url, until_user)
    until_owner = CurrentUser(id=until_user)
    async with claimed_transaction(factory, until_owner) as session:
        await create_spirit_if_absent(session, until_owner, _request())
        await session.execute(
            text(
                "UPDATE public.spirits SET status = 'study', "
                "study_until = :until, last_interact_at = :last "
                "WHERE user_id = :user_id"
            ),
            {
                "until": NOW - timedelta(seconds=1),
                "last": NOW - timedelta(hours=1),
                "user_id": until_user,
            },
        )
        result = await settle_spirit_state(session, until_owner, now=NOW)
        row = await _spirit_row(session, until_user)
    assert result.changed is True
    assert result.status == "home"
    assert result.version == 2
    assert row.status == "home"
    assert row.study_until is None
    assert row.last_interact_at == NOW - timedelta(hours=1)
    async with claimed_transaction(factory, until_owner) as session:
        repeat = await settle_spirit_state(session, until_owner, now=NOW)
    assert repeat.changed is False
    assert repeat.version == 2
    await engine.dispose()


def test_concurrent_settle_bumps_once_and_writes_one_event() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_concurrent_lost_settle(url))


async def _assert_concurrent_lost_settle(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=8, max_overflow=4)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    last_interact = NOW - timedelta(hours=72)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _request())
        await _set_last_interact(session, user_id, last_interact)

    async def _one() -> Any:
        async with claimed_transaction(factory, user) as session:
            return await settle_spirit_state(session, user, now=NOW)

    outcomes = await asyncio.gather(*[_one() for _ in range(8)])
    changed = [item for item in outcomes if item.changed]
    assert len(changed) == 1
    assert {item.status for item in outcomes} == {"lost"}
    assert {item.version for item in outcomes} == {2}
    assert {item.spirit_id for item in outcomes} == {created.spirit_id}
    assert all(item.last_interact_at == last_interact for item in outcomes)
    async with claimed_transaction(factory, user) as session:
        row = await _spirit_row(session, user_id)
        stable = await settle_spirit_state(session, user, now=NOW)
    assert row.status == "lost"
    assert row.version == 2
    assert row.has_been_lost is True
    assert row.last_interact_at == last_interact
    assert stable.changed is False
    assert stable.version == 2
    outbox = await _outbox_rows(url, user_id)
    assert len(outbox) == 1
    assert outbox[0].event_type == STATE_SETTLE_EVENT
    assert ":home:lost:2" in str(outbox[0].dedupe_key)
    await engine.dispose()
