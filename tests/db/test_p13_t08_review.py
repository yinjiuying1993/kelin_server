"""P13-T08 live review: scheduler, clock, RLS, concurrency, cost metadata."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.ai_usage import UsageSample
from app.domain.feed import KNOWLEDGE_DAILY_LIMIT
from app.repositories import feed as feed_repo
from app.schemas.spirit import CreateSpiritRequest
from app.services.ai_usage import record_ai_usage
from app.services.growth import record_and_apply
from app.services.quota import consume as consume_quota
from app.services.spirit import create_spirit_if_absent
from app.services.state_scheduler import process_due_state_ticks, scan_due_state_jobs
from app.services.usage_scheduler import process_usage_rollup_jobs, scan_usage_rollup_jobs
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)
CONCURRENT = 24


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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_p13_t08_scheduler_rls_quota_and_cost() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_review(url))


async def _assert_review(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=16, max_overflow=16)
    factory = create_session_factory(engine)
    try:
        await _assert_role_and_function_grants(url, factory)
        await _assert_72h_scheduler_runs(url, factory)
        await _assert_growth_isolation_and_once(url, factory)
        await _assert_quota_does_not_over_issue(factory)
        await _assert_usage_rollup_actually_runs(url, factory)
        await _assert_no_client_policies(url)
    finally:
        await engine.dispose()


async def _assert_role_and_function_grants(url: str, factory: Any) -> None:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        scheduler_bypass = await conn.scalar(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'kelin_scheduler'")
        )
        definer_bypass = await conn.scalar(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'kelin_definer'")
        )
        definer_login = await conn.scalar(
            text("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'kelin_definer'")
        )
        api_enqueue = await conn.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_api', 'private.enqueue_due_state_jobs(timestamptz)', 'EXECUTE')"
            )
        )
        worker_enqueue = await conn.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_worker', 'private.enqueue_due_state_jobs(timestamptz)', 'EXECUTE')"
            )
        )
        scheduler_enqueue = await conn.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_scheduler', 'private.enqueue_due_state_jobs(timestamptz)', 'EXECUTE')"
            )
        )
        scheduler_growth = await conn.scalar(
            text("SELECT has_table_privilege('kelin_scheduler', 'public.growth_events', 'INSERT')")
        )
        scheduler_usage = await conn.scalar(
            text("SELECT has_table_privilege('kelin_scheduler', 'public.ai_usage', 'INSERT')")
        )
        worker_rollup = await conn.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_worker', 'private.rollup_ai_usage(date)', 'EXECUTE')"
            )
        )
        api_rollup = await conn.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_api', 'private.rollup_ai_usage(date)', 'EXECUTE')"
            )
        )
    await engine.dispose()
    assert scheduler_bypass is False
    assert definer_bypass is True
    assert definer_login is False
    assert api_enqueue is False
    assert worker_enqueue is False
    assert scheduler_enqueue is True
    assert scheduler_growth is False
    assert scheduler_usage is False
    assert worker_rollup is True
    assert api_rollup is False

    async with claimed_transaction(factory, None, db_role="kelin_api") as session:
        try:
            await session.execute(text("SELECT private.enqueue_due_state_jobs(:now)"), {"now": NOW})
        except ProgrammingError:
            return
        raise AssertionError("kelin_api must not execute enqueue_due_state_jobs")


async def _assert_72h_scheduler_runs(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    last_interact = NOW - timedelta(hours=72)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        await session.execute(
            text("UPDATE public.spirits SET last_interact_at = :ts WHERE user_id = :user_id"),
            {"ts": last_interact, "user_id": owner},
        )

    scanned = await scan_due_state_jobs(factory, now=NOW)
    assert scanned.inserted == 1
    processed = await process_due_state_ticks(factory, now=NOW)
    assert len(processed) == 1
    assert processed[0].spirit_id == created.spirit_id
    assert processed[0].changed is True
    assert processed[0].applied_time_passed is True

    async with claimed_transaction(factory, user) as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, version, last_interact_at, has_been_lost "
                    "FROM public.spirits WHERE user_id = :user_id"
                ),
                {"user_id": owner},
            )
        ).one()
        events = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'time_passed'"
            ),
            {"spirit_id": created.spirit_id},
        )
        payload = await session.scalar(
            text(
                "SELECT payload FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'time_passed'"
            ),
            {"spirit_id": created.spirit_id},
        )
    assert str(row.status) == "lost"
    assert int(row.version) == 2
    assert row.last_interact_at == last_interact
    assert bool(row.has_been_lost) is True
    assert int(events or 0) == 1
    assert payload == {}
    blob = str(payload)
    assert "prompt" not in blob
    assert "content" not in blob

    again = await process_due_state_ticks(factory, now=NOW)
    assert again == ()
    async with claimed_transaction(factory, user) as session:
        stable = await session.scalar(
            text("SELECT version FROM public.spirits WHERE user_id = :user_id"),
            {"user_id": owner},
        )
        still = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND event_type = 'time_passed'"
            ),
            {"spirit_id": created.spirit_id},
        )
    assert int(stable or 0) == 2
    assert int(still or 0) == 1


async def _assert_growth_isolation_and_once(url: str, factory: Any) -> None:
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()
    await _insert_auth_user(url, owner_a)
    await _insert_auth_user(url, owner_b)
    user_a = CurrentUser(id=owner_a)
    user_b = CurrentUser(id=owner_b)
    source_id = uuid.uuid4()
    async with claimed_transaction(factory, user_a) as session:
        created_a = await create_spirit_if_absent(session, user_a, _spirit_request())
    async with claimed_transaction(factory, user_b) as session:
        await create_spirit_if_absent(session, user_b, _spirit_request())

    async def _one() -> None:
        async with claimed_transaction(factory, user_a) as session:
            locked = await feed_repo.lock_owned_spirit(session, user_a.id)
            assert locked is not None
            await record_and_apply(
                session,
                owner_id=user_a.id,
                spirit_id=locked.id,
                event_type="feed_accepted",
                source_id=source_id,
                payload={"hunger_delta": 10},
                now=NOW,
                touch_interact=True,
            )

    await asyncio.gather(*[_one() for _ in range(8)])
    async with claimed_transaction(factory, user_a) as session:
        own = await session.scalar(
            text(
                "SELECT count(*) FROM public.growth_events "
                "WHERE spirit_id = :spirit_id AND source_id = :source_id"
            ),
            {"spirit_id": created_a.spirit_id, "source_id": source_id},
        )
        hunger = await session.scalar(
            text("SELECT hunger FROM public.spirits WHERE id = :id"),
            {"id": created_a.spirit_id},
        )
    async with claimed_transaction(factory, user_b) as session:
        leaked = await session.scalar(
            text("SELECT count(*) FROM public.growth_events WHERE spirit_id = :spirit_id"),
            {"spirit_id": created_a.spirit_id},
        )
    assert int(own or 0) == 1
    assert int(hunger or 0) == 90
    assert int(leaked or 0) == 0


async def _assert_quota_does_not_over_issue(factory: Any) -> None:
    owner = uuid.uuid4()
    user = CurrentUser(id=owner)

    async def _attempt() -> str:
        async with claimed_transaction(factory, user) as session:
            try:
                await consume_quota(
                    session,
                    owner,
                    "knowledge",
                    now=NOW,
                    timezone="Asia/Shanghai",
                )
            except ApiError as exc:
                assert exc.code == "QUOTA_EXCEEDED"
                return "exceeded"
            return "ok"

    outcomes = await asyncio.gather(*[_attempt() for _ in range(CONCURRENT)])
    async with claimed_transaction(factory, user) as session:
        used = await session.scalar(
            text(
                "SELECT used FROM public.daily_usage "
                "WHERE user_id = :user_id AND capability = 'knowledge'"
            ),
            {"user_id": owner},
        )
    assert outcomes.count("ok") == KNOWLEDGE_DAILY_LIMIT
    assert outcomes.count("exceeded") == CONCURRENT - KNOWLEDGE_DAILY_LIMIT
    assert int(used or 0) == KNOWLEDGE_DAILY_LIMIT


async def _assert_usage_rollup_actually_runs(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        recorded = await record_ai_usage(
            session,
            owner,
            UsageSample(
                request_id=uuid.uuid4(),
                capability="chat",
                success=True,
                model_alias="chat",
                prompt_version="chat/v3",
                input_units=4,
                output_units=2,
                error_code=None,
            ),
            now=YESTERDAY,
        )
    assert recorded.estimated_cost_micros > 0
    first = await scan_usage_rollup_jobs(factory, now=NOW)
    second = await scan_usage_rollup_jobs(factory, now=NOW)
    assert first == 1
    assert second == 0
    rollups = await process_usage_rollup_jobs(factory, now=NOW)
    assert len(rollups) == 1
    assert rollups[0].request_count == 1
    assert rollups[0].total_micros == recorded.estimated_cost_micros
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        names = (
            await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'ai_usage'"
                )
            )
        ).all()
        prompt_version = await conn.scalar(
            text("SELECT prompt_version FROM public.ai_usage WHERE user_id = :user_id"),
            {"user_id": owner},
        )
    await engine.dispose()
    columns = {str(row.column_name) for row in names}
    assert "prompt" not in columns
    assert "content" not in columns
    assert str(prompt_version) == "chat/v3"


async def _assert_no_client_policies(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        client = (
            await conn.execute(
                text(
                    "SELECT tablename, policyname FROM pg_policies "
                    "WHERE schemaname = 'public' "
                    "AND tablename IN ('growth_events', 'daily_usage', 'ai_usage') "
                    "AND ('anon' = ANY (roles) OR 'authenticated' = ANY (roles))"
                )
            )
        ).all()
        daily_delete = await conn.scalar(
            text("SELECT has_table_privilege('kelin_api', 'public.daily_usage', 'DELETE')")
        )
    await engine.dispose()
    assert client == []
    assert daily_delete is False
