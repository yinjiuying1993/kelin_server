from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.visits import VISIT_SETTLE_EVENT, visit_settle_dedupe_key
from app.schemas.social_api import AddFriendRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.friends import add_owned_friend
from app.services.spirit import create_spirit_if_absent
from app.services.visit_planner import handle_visit_plan
from app.services.visit_settle import process_due_visit_settles, scan_due_visit_settle_jobs
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)


def _spirit_request(*, name: str) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": name,
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


async def _boot(
    url: str, factory: Any, user_id: uuid.UUID, *, name: str
) -> tuple[CurrentUser, uuid.UUID, str]:
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        spirit = await create_spirit_if_absent(session, user, _spirit_request(name=name))
    return user, spirit.spirit_id, spirit.invite_code


async def _set_idle(session: AsyncSession, user_id: uuid.UUID, *, now: datetime) -> None:
    await session.execute(
        text(
            "UPDATE public.spirits SET last_interact_at = :ts, status = 'away' "
            "WHERE user_id = :user_id"
        ),
        {"ts": now - timedelta(hours=18), "user_id": user_id},
    )


async def _add_friend(factory: Any, user: CurrentUser, invite_code: str) -> None:
    async with claimed_transaction(factory, user) as session:
        await add_owned_friend(
            session,
            user,
            AddFriendRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "invite_code": invite_code}
            ),
            now=NOW,
        )


async def _visits(url: str, visitor_id: uuid.UUID) -> list[Any]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, destination_index, status, settled_at "
                    "FROM public.visits WHERE visitor_spirit_id = :id "
                    "ORDER BY destination_index ASC"
                ),
                {"id": visitor_id},
            )
        ).all()
    await engine.dispose()
    return list(rows)


async def _cards(url: str, visit_id: uuid.UUID) -> list[Any]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT receiver_spirit_id, sender_spirit_id, npc_id, text "
                    "FROM public.postcards WHERE visit_id = :id ORDER BY created_at, id"
                ),
                {"id": visit_id},
            )
        ).all()
    await engine.dispose()
    return list(rows)


def test_visit_settle_independent_lease_retry_dead_and_home() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_settle(url))


async def _assert_settle(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=16, max_overflow=8)
    factory = create_session_factory(engine)
    try:
        await _assert_grants(factory)
        await _assert_two_destinations_isolated(url, factory)
        await _assert_dual_worker_and_repeat(url, factory)
        await _assert_lease_retry_then_dead(url, factory)
    finally:
        await engine.dispose()


async def _assert_grants(factory: Any) -> None:
    async with claimed_transaction(factory, None, db_role="kelin_api") as session:
        worker = await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_worker', 'private.settle_visit(uuid, timestamptz, text, text)', 'EXECUTE')"
            )
        )
        api = await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_api', 'private.settle_visit(uuid, timestamptz, text, text)', 'EXECUTE')"
            )
        )
        args = await session.scalar(
            text(
                "SELECT pg_get_function_identity_arguments(p.oid) "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'private' AND p.proname = 'settle_visit'"
            )
        )
    assert worker is True
    assert api is False
    assert "uuid" in str(args) and "timestamp with time zone" in str(args)
    assert "text" in str(args)
    assert str(args).count(",") == 3
    async with claimed_transaction(factory, None, db_role="kelin_api") as session:
        try:
            await session.execute(
                text("SELECT private.settle_visit(:id, :now, :visitor_text, :host_text)"),
                {"id": uuid.uuid4(), "now": NOW, "visitor_text": "", "host_text": ""},
            )
        except ProgrammingError:
            return
        raise AssertionError("kelin_api must not execute settle_visit")


async def _plan_two_hosts(url: str, factory: Any) -> tuple[uuid.UUID, uuid.UUID, list[Any]]:
    visitor_id = uuid.uuid4()
    host_a = uuid.uuid4()
    host_b = uuid.uuid4()
    visitor, visitor_spirit, _ = await _boot(url, factory, visitor_id, name="串门客")
    _ha, host_a_spirit, code_a = await _boot(url, factory, host_a, name="甲居")
    _hb, host_b_spirit, code_b = await _boot(url, factory, host_b, name="乙居")
    await _add_friend(factory, visitor, code_a)
    await _add_friend(factory, visitor, code_b)
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW)
    await handle_visit_plan(factory, now=NOW)
    rows = await _visits(url, visitor_spirit)
    assert len(rows) == 2
    return visitor_id, visitor_spirit, rows


async def _assert_two_destinations_isolated(url: str, factory: Any) -> None:
    visitor_id, visitor_spirit, rows = await _plan_two_hosts(url, factory)
    scan = await scan_due_visit_settle_jobs(factory, now=NOW)
    assert scan.inserted == 2
    first = await process_due_visit_settles(factory, now=NOW, limit=1, worker_id="w-one")
    assert len(first) == 1
    assert first[0].changed is True
    after = await _visits(url, visitor_spirit)
    settled = [row for row in after if str(row.status) == "settled"]
    open_rows = [row for row in after if str(row.status) == "eligible"]
    assert len(settled) == 1
    assert len(open_rows) == 1
    cards = await _cards(url, settled[0].id)
    assert len(cards) == 2
    assert all("记忆" not in str(card.text) and "user_id" not in str(card.text) for card in cards)
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        status = await conn.scalar(
            text("SELECT status FROM public.spirits WHERE id = :id"),
            {"id": visitor_spirit},
        )
        growth = await conn.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE source_id = :id AND event_type = 'visit_completed'"
            ),
            {"id": settled[0].id},
        )
    await engine.dispose()
    assert str(status) == "away"
    assert int(growth or 0) == 1

    leftover = open_rows[0].id
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.outbox_events SET status = 'claimed', attempt_count = 8, "
                "lease_expires_at = :expired, locked_by = 'stale' "
                "WHERE dedupe_key = :key"
            ),
            {
                "expired": NOW - timedelta(seconds=5),
                "key": visit_settle_dedupe_key(leftover),
            },
        )
    await engine.dispose()
    await process_due_visit_settles(factory, now=NOW, limit=8, worker_id="w-dead")
    final_rows = await _visits(url, visitor_spirit)
    by_id = {row.id: row for row in final_rows}
    assert str(by_id[settled[0].id].status) == "settled"
    assert str(by_id[leftover].status) == "failed"
    assert await _cards(url, leftover) == []
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        home = (
            await conn.execute(
                text("SELECT status, has_visited FROM public.spirits WHERE id = :id"),
                {"id": visitor_spirit},
            )
        ).one()
        job_status = await conn.scalar(
            text("SELECT status FROM public.outbox_events WHERE dedupe_key = :key"),
            {"key": visit_settle_dedupe_key(leftover)},
        )
    await engine.dispose()
    assert str(home.status) == "home"
    assert bool(home.has_visited) is True
    assert str(job_status) == "dead"
    repeat_cards = await _cards(url, settled[0].id)
    assert len(repeat_cards) == 2


async def _assert_dual_worker_and_repeat(url: str, factory: Any) -> None:
    _visitor_id, visitor_spirit, rows = await _plan_two_hosts(url, factory)
    await scan_due_visit_settle_jobs(factory, now=NOW)
    left, right = await asyncio.gather(
        process_due_visit_settles(factory, now=NOW, limit=1, worker_id="w-a"),
        process_due_visit_settles(factory, now=NOW, limit=1, worker_id="w-b"),
    )
    ticks = [*left, *right]
    assert {tick.visit_id for tick in ticks if tick.outcome == "done"} == {rows[0].id, rows[1].id}
    after = await _visits(url, visitor_spirit)
    assert {str(row.status) for row in after} == {"settled"}
    for row in after:
        assert len(await _cards(url, row.id)) == 2
    again = await process_due_visit_settles(factory, now=NOW, limit=8, worker_id="w-repeat")
    assert again == ()
    for row in after:
        assert len(await _cards(url, row.id)) == 2
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        home = await conn.scalar(
            text("SELECT status FROM public.spirits WHERE id = :id"),
            {"id": visitor_spirit},
        )
        jobs = await conn.scalar(
            text(
                "SELECT count(*) FROM public.outbox_events "
                "WHERE event_type = :event AND status = 'done' "
                "AND (aggregate_id = :a OR aggregate_id = :b)"
            ),
            {"event": VISIT_SETTLE_EVENT, "a": rows[0].id, "b": rows[1].id},
        )
    await engine.dispose()
    assert str(home) == "home"
    assert int(jobs or 0) == 2


async def _assert_lease_retry_then_dead(url: str, factory: Any) -> None:
    visitor_id = uuid.uuid4()
    visitor, visitor_spirit, _ = await _boot(url, factory, visitor_id, name="独行")
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW)
    await handle_visit_plan(factory, now=NOW)
    rows = await _visits(url, visitor_spirit)
    assert len(rows) == 2
    await scan_due_visit_settle_jobs(factory, now=NOW)
    first = rows[0].id
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.outbox_events SET status = 'claimed', attempt_count = 1, "
                "locked_by = 'old', lease_expires_at = :expired "
                "WHERE dedupe_key = :key"
            ),
            {"expired": NOW - timedelta(seconds=1), "key": visit_settle_dedupe_key(first)},
        )
    await engine.dispose()
    reclaimed = await process_due_visit_settles(
        factory, now=NOW, limit=8, worker_id="w-reclaim"
    )
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        job_status = await conn.scalar(
            text("SELECT status FROM public.outbox_events WHERE dedupe_key = :key"),
            {"key": visit_settle_dedupe_key(first)},
        )
    await engine.dispose()
    assert str(job_status) == "retry"
    assert first not in {tick.visit_id for tick in reclaimed if tick.outcome == "done"}
    after_reclaim = await _visits(url, visitor_spirit)
    by_id = {row.id: row for row in after_reclaim}
    assert str(by_id[first].status) == "eligible"
    assert {str(row.status) for row in after_reclaim} == {"eligible", "settled"}
    retried = await process_due_visit_settles(
        factory, now=NOW + timedelta(minutes=5), limit=8, worker_id="w-retry"
    )
    settled_ids = {tick.visit_id for tick in retried if tick.outcome == "done"}
    assert first in settled_ids
    after = await _visits(url, visitor_spirit)
    assert {str(row.status) for row in after} == {"settled"}
    npc_cards = await _cards(url, first)
    assert len(npc_cards) == 1
    assert npc_cards[0].npc_id in {"fog", "lamp"}
    assert npc_cards[0].sender_spirit_id is None
    assert "字条" in str(npc_cards[0].text)
