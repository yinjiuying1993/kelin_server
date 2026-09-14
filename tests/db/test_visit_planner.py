from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.visits import (
    VISIT_PLAN_EVENT,
    visit_eligibility_key,
    visit_plan_dedupe_key,
)
from app.schemas.social_api import (
    PUBLIC_VISIT_CONTEXT_FIELD_NAMES,
    AddFriendRequest,
)
from app.schemas.spirit import CreateSpiritRequest
from app.services.friends import add_owned_friend
from app.services.spirit import create_spirit_if_absent
from app.services.visit_planner import (
    handle_visit_plan,
    process_due_visit_plans,
    scan_due_visit_plan_jobs,
)
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
BUCKET_EDGE = datetime(2026, 9, 12, 11, 59, 59, tzinfo=UTC)
NEXT_BUCKET = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
PRIVATE = frozenset({"user_id", "memory", "conversation", "latitude", "longitude"})


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


async def _set_idle(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime, hours: float = 18
) -> None:
    await session.execute(
        text("UPDATE public.spirits SET last_interact_at = :ts WHERE user_id = :user_id"),
        {"ts": now - timedelta(hours=hours), "user_id": user_id},
    )


async def _set_visit_on(session: AsyncSession, user_id: uuid.UUID, enabled: bool) -> None:
    await session.execute(
        text("UPDATE public.user_preferences SET visit_on = :on WHERE user_id = :user_id"),
        {"on": enabled, "user_id": user_id},
    )


async def _add_friend(
    factory: Any, user: CurrentUser, invite_code: str, *, now: datetime
) -> None:
    async with claimed_transaction(factory, user) as session:
        await add_owned_friend(
            session,
            user,
            AddFriendRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "invite_code": invite_code}
            ),
            now=now,
        )


async def _visits_for(url: str, visitor_id: uuid.UUID) -> list[Any]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, plan_id, destination_index, host_spirit_id, npc_id, "
                    "npc_config_version, status, eligibility_key, public_context "
                    "FROM public.visits WHERE visitor_spirit_id = :id "
                    "ORDER BY destination_index ASC, created_at ASC, id ASC"
                ),
                {"id": visitor_id},
            )
        ).all()
    await engine.dispose()
    return list(rows)


def _assert_public_context(context: Any) -> None:
    payload = context if isinstance(context, dict) else json.loads(json.dumps(context))
    assert set(payload) <= PUBLIC_VISIT_CONTEXT_FIELD_NAMES
    assert PRIVATE.isdisjoint(payload)
    assert payload["weather"] == "cloudy"
    assert payload["title"]
    assert payload["stage"] in {"whelp", "formed", "awake"}


def test_visit_planner_destinations_rate_npc_and_grants() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_planner(url))


async def _assert_planner(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=12, max_overflow=8)
    factory = create_session_factory(engine)
    try:
        await _assert_role_grants(factory)
        await _assert_zero_when_not_idle(url, factory)
        await _assert_no_friends_uses_npc(url, factory)
        await _assert_two_hosts_and_repeat_then_boundary(url, factory)
        await _assert_visit_off_host_falls_back_to_npc(url, factory)
        await _assert_one_destination_when_npcs_disabled(url, factory)
        await _assert_scheduler_dedupe_and_worker(url, factory)
    finally:
        await engine.dispose()


async def _assert_role_grants(factory: Any) -> None:
    async with claimed_transaction(factory, None, db_role="kelin_api") as session:
        worker_plan = await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_worker', 'private.plan_visits(timestamptz)', 'EXECUTE')"
            )
        )
        api_plan = await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_api', 'private.plan_visits(timestamptz)', 'EXECUTE')"
            )
        )
        scheduler_plan = await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_scheduler', 'private.plan_visits(timestamptz)', 'EXECUTE')"
            )
        )
        api_enqueue = await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_api', 'private.enqueue_due_visit_plan_jobs(timestamptz)', 'EXECUTE')"
            )
        )
        scheduler_enqueue = await session.scalar(
            text(
                "SELECT has_function_privilege("
                "'kelin_scheduler', 'private.enqueue_due_visit_plan_jobs(timestamptz)', 'EXECUTE')"
            )
        )
        args = (
            await session.execute(
                text(
                    "SELECT pg_get_function_identity_arguments(p.oid) "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'private' AND p.proname = 'plan_visits'"
                )
            )
        ).scalar_one()
    assert worker_plan is True
    assert api_plan is False
    assert scheduler_plan is False
    assert api_enqueue is False
    assert scheduler_enqueue is True
    assert "timestamp with time zone" in str(args)
    assert "uuid" not in str(args)
    assert "," not in str(args)

    async with claimed_transaction(factory, None, db_role="kelin_api") as session:
        try:
            await session.execute(text("SELECT private.plan_visits(:now)"), {"now": NOW})
        except ProgrammingError:
            pass
        else:
            raise AssertionError("kelin_api must not execute plan_visits")

    async with claimed_transaction(factory, None, db_role="kelin_api") as session:
        try:
            await session.execute(
                text("SELECT private.enqueue_due_visit_plan_jobs(:now)"), {"now": NOW}
            )
        except ProgrammingError:
            return
        raise AssertionError("kelin_api must not execute enqueue_due_visit_plan_jobs")


async def _assert_zero_when_not_idle(url: str, factory: Any) -> None:
    user_id = uuid.uuid4()
    user, spirit_id, _ = await _boot(url, factory, user_id, name="在家")
    async with claimed_transaction(factory, user) as session:
        await _set_idle(session, user_id, now=NOW, hours=17)
    planned = await handle_visit_plan(factory, now=NOW)
    assert planned.inserted == 0
    assert await _visits_for(url, spirit_id) == []

    off_id = uuid.uuid4()
    off_user, off_spirit, _ = await _boot(url, factory, off_id, name="拒出")
    async with claimed_transaction(factory, off_user) as session:
        await _set_visit_on(session, off_id, False)
        await _set_idle(session, off_id, now=NOW, hours=18)
    off_planned = await handle_visit_plan(factory, now=NOW)
    assert off_planned.inserted == 0
    assert await _visits_for(url, off_spirit) == []

    lost_id = uuid.uuid4()
    lost_user, lost_spirit, _ = await _boot(url, factory, lost_id, name="走失")
    async with claimed_transaction(factory, lost_user) as session:
        await session.execute(
            text(
                "UPDATE public.spirits SET status = 'lost', last_interact_at = :ts "
                "WHERE user_id = :user_id"
            ),
            {"ts": NOW - timedelta(hours=18), "user_id": lost_id},
        )
    lost_planned = await handle_visit_plan(factory, now=NOW)
    assert lost_planned.inserted == 0
    assert await _visits_for(url, lost_spirit) == []


async def _assert_no_friends_uses_npc(url: str, factory: Any) -> None:
    user_id = uuid.uuid4()
    user, spirit_id, _ = await _boot(url, factory, user_id, name="独行")
    async with claimed_transaction(factory, user) as session:
        await _set_idle(session, user_id, now=NOW, hours=18)
    planned = await handle_visit_plan(factory, now=NOW)
    rows = await _visits_for(url, spirit_id)
    assert planned.inserted == 2
    assert len(rows) == 2
    assert [int(row.destination_index) for row in rows] == [1, 2]
    assert rows[0].plan_id == rows[1].plan_id
    assert rows[0].host_spirit_id is None and rows[1].host_spirit_id is None
    assert [row.npc_id for row in rows] == ["fog", "lamp"]
    assert all(int(row.npc_config_version) == 1 for row in rows)
    assert all(str(row.status) == "eligible" for row in rows)
    for row, index in zip(rows, (1, 2), strict=True):
        assert str(row.eligibility_key) == visit_eligibility_key(
            spirit_id, now=NOW, destination_index=index
        )
        _assert_public_context(row.public_context)
        assert row.public_context["title"] in {"雾里的那只", "守灯的那只"}
        assert row.public_context["stage"] == "formed"


async def _assert_two_hosts_and_repeat_then_boundary(url: str, factory: Any) -> None:
    visitor_id = uuid.uuid4()
    host_a_id = uuid.uuid4()
    host_b_id = uuid.uuid4()
    visitor, visitor_spirit, _ = await _boot(url, factory, visitor_id, name="串门客")
    _host_a, host_a_spirit, code_a = await _boot(url, factory, host_a_id, name="甲居")
    _host_b, host_b_spirit, code_b = await _boot(url, factory, host_b_id, name="乙居")
    await _add_friend(factory, visitor, code_a, now=NOW)
    await _add_friend(factory, visitor, code_b, now=NOW)
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW, hours=18)
    first = await handle_visit_plan(factory, now=NOW)
    rows = await _visits_for(url, visitor_spirit)
    assert first.inserted == 2
    assert len(rows) == 2
    assert {int(row.destination_index) for row in rows} == {1, 2}
    assert rows[0].plan_id == rows[1].plan_id
    assert {row.host_spirit_id for row in rows} == {host_a_spirit, host_b_spirit}
    assert all(row.npc_id is None for row in rows)
    for row in rows:
        _assert_public_context(row.public_context)
        assert row.public_context["title"] in {"甲居", "乙居"}
        assert row.public_context["stage"] == "whelp"

    repeat = await handle_visit_plan(factory, now=BUCKET_EDGE)
    assert repeat.inserted == 0
    assert len(await _visits_for(url, visitor_spirit)) == 2

    later = await handle_visit_plan(factory, now=NEXT_BUCKET)
    after = await _visits_for(url, visitor_spirit)
    assert later.inserted >= 2
    assert len(after) == 4
    new_rows = [
        row
        for row in after
        if str(row.eligibility_key).endswith(":2026091212:1")
        or str(row.eligibility_key).endswith(":2026091212:2")
    ]
    assert len(new_rows) == 2
    assert new_rows[0].plan_id == new_rows[1].plan_id
    assert new_rows[0].plan_id != rows[0].plan_id
    assert max(int(row.destination_index) for row in after) == 2
    assert all(int(row.destination_index) in (1, 2) for row in after)


async def _assert_visit_off_host_falls_back_to_npc(url: str, factory: Any) -> None:
    visitor_id = uuid.uuid4()
    open_id = uuid.uuid4()
    closed_id = uuid.uuid4()
    visitor, visitor_spirit, _ = await _boot(url, factory, visitor_id, name="访客")
    _open_host, open_spirit, open_code = await _boot(url, factory, open_id, name="开访")
    closed_host, closed_spirit, closed_code = await _boot(url, factory, closed_id, name="拒访")
    await _add_friend(factory, visitor, open_code, now=NOW)
    await _add_friend(factory, visitor, closed_code, now=NOW)
    async with claimed_transaction(factory, closed_host) as session:
        await _set_visit_on(session, closed_id, False)
    async with claimed_transaction(factory, visitor) as session:
        await _set_idle(session, visitor_id, now=NOW, hours=18)
        await _set_idle(session, open_id, now=NOW, hours=1)
        await _set_idle(session, closed_id, now=NOW, hours=1)
    planned = await handle_visit_plan(factory, now=NOW)
    rows = await _visits_for(url, visitor_spirit)
    assert planned.inserted == 2
    assert len(rows) == 2
    assert int(rows[0].destination_index) == 1
    assert rows[0].host_spirit_id == open_spirit
    assert rows[0].npc_id is None
    assert rows[1].host_spirit_id is None
    assert rows[1].npc_id == "fog"
    assert closed_spirit not in {row.host_spirit_id for row in rows}
    for row in rows:
        _assert_public_context(row.public_context)


async def _assert_one_destination_when_npcs_disabled(url: str, factory: Any) -> None:
    visitor_id = uuid.uuid4()
    host_id = uuid.uuid4()
    visitor, visitor_spirit, _ = await _boot(url, factory, visitor_id, name="单程")
    _host, host_spirit, code = await _boot(url, factory, host_id, name="唯一")
    await _add_friend(factory, visitor, code, now=NOW)
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE public.npc_profiles SET enabled = false"))
        async with claimed_transaction(factory, visitor) as session:
            await _set_idle(session, visitor_id, now=NOW, hours=18)
        planned = await handle_visit_plan(factory, now=NOW)
        rows = await _visits_for(url, visitor_spirit)
        assert planned.inserted == 1
        assert len(rows) == 1
        assert int(rows[0].destination_index) == 1
        assert rows[0].host_spirit_id == host_spirit
        assert rows[0].npc_id is None
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE public.npc_profiles SET enabled = true"))
        await engine.dispose()


async def _assert_scheduler_dedupe_and_worker(url: str, factory: Any) -> None:
    user_id = uuid.uuid4()
    user, spirit_id, _ = await _boot(url, factory, user_id, name="扫表")
    async with claimed_transaction(factory, user) as session:
        await _set_idle(session, user_id, now=NOW, hours=18)
    scans = await asyncio.gather(*[scan_due_visit_plan_jobs(factory, now=NOW) for _ in range(6)])
    assert sum(item.inserted for item in scans) >= 1
    repeat = await scan_due_visit_plan_jobs(factory, now=NOW)
    assert repeat.inserted == 0
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        job = (
            await conn.execute(
                text(
                    "SELECT dedupe_key, event_type, status FROM public.outbox_events "
                    "WHERE aggregate_id = :id AND event_type = :event"
                ),
                {"id": spirit_id, "event": VISIT_PLAN_EVENT},
            )
        ).one()
    await engine.dispose()
    assert str(job.dedupe_key) == visit_plan_dedupe_key(spirit_id, now=NOW)
    assert str(job.event_type) == VISIT_PLAN_EVENT
    processed = await process_due_visit_plans(factory, now=NOW)
    assert processed.inserted == 2
    assert len(processed.job_ids) >= 1
    rows = await _visits_for(url, spirit_id)
    assert len(rows) == 2
    assert [row.npc_id for row in rows] == ["fog", "lamp"]
    again = await process_due_visit_plans(factory, now=NOW)
    assert again.inserted == 0
    assert again.job_ids == ()
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        status = await conn.scalar(
            text(
                "SELECT status FROM public.outbox_events "
                "WHERE aggregate_id = :id AND event_type = :event"
            ),
            {"id": spirit_id, "event": VISIT_PLAN_EVENT},
        )
        postcards = await conn.scalar(text("SELECT count(*) FROM public.postcards"))
        proc_args = await conn.scalar(
            text(
                "SELECT pg_get_functiondef(p.oid) "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'private' AND p.proname = 'plan_visits'"
            )
        )
    await engine.dispose()
    assert str(status) == "done"
    assert int(postcards or 0) == 0
    assert "settle_visit" not in str(proc_args)
    assert "INSERT INTO public.postcards" not in str(proc_args)
