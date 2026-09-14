from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.quota import RESERVATION_TTL, local_usage_date, quota_usage
from app.schemas.spirit import CreateSpiritRequest
from app.services.quota import consume as consume_quota
from app.services.quota import expire_due_reservations, hold, reserve
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
HOP_NOW = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)
CONCURRENT = 100
KNOWLEDGE_LIMIT = 10


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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _usage(session: AsyncSession, user_id: uuid.UUID, capability: str) -> Any:
    return (
        await session.execute(
            text(
                "SELECT used, reserved, limit_value, usage_date, timezone, version "
                "FROM public.daily_usage "
                "WHERE user_id = :user_id AND capability = :capability"
            ),
            {"user_id": user_id, "capability": capability},
        )
    ).first()


def test_atomic_daily_usage_quota() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_quota_behaviors(url))


async def _assert_quota_behaviors(url: str) -> None:
    await _assert_private_reservation_table(url)
    engine = create_runtime_engine(url, pool_size=20, max_overflow=40)
    factory = create_session_factory(engine)
    try:
        await _assert_concurrent_knowledge(url, factory)
        await _assert_timezone_hop(url, factory)
        await _assert_hold_releases_on_provider_failure(url, factory)
        await _assert_expire_due_reservations(url, factory)
        await _assert_stable_429_details(url, factory)
        await _assert_reserve_is_idempotent(url, factory)
    finally:
        await engine.dispose()


async def _assert_private_reservation_table(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        flags = (
            await conn.execute(
                text(
                    "SELECT c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'private' AND c.relname = 'quota_reservations'"
                )
            )
        ).one()
        public_policies = await conn.scalar(
            text(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname = 'public' AND tablename = 'quota_reservations'"
            )
        )
        private_policies = (
            await conn.execute(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = 'private' AND tablename = 'quota_reservations' "
                    "ORDER BY policyname"
                )
            )
        ).all()
    await engine.dispose()
    assert bool(flags.relrowsecurity) is True
    assert bool(flags.relforcerowsecurity) is True
    assert int(public_policies or 0) == 0
    names = [str(row.policyname) for row in private_policies]
    assert names == [
        "rls_quota_reservations_kelin_api_all",
        "rls_quota_reservations_kelin_worker_all",
    ]


async def _assert_concurrent_knowledge(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
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
                assert exc.status_code == 429
                assert exc.retryable is False
                return "exceeded"
            return "ok"

    outcomes = await asyncio.gather(*[_attempt() for _ in range(CONCURRENT)])
    async with claimed_transaction(factory, user) as session:
        row = await _usage(session, owner, "knowledge")
    assert outcomes.count("ok") == KNOWLEDGE_LIMIT
    assert outcomes.count("exceeded") == CONCURRENT - KNOWLEDGE_LIMIT
    assert row is not None
    assert int(row.used) == KNOWLEDGE_LIMIT
    assert int(row.reserved) == 0
    assert int(row.limit_value) == KNOWLEDGE_LIMIT


async def _assert_timezone_hop(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())
        await session.execute(
            text(
                "UPDATE public.user_preferences "
                "SET timezone = 'America/Los_Angeles' WHERE user_id = :user_id"
            ),
            {"user_id": owner},
        )
        first = await consume_quota(session, owner, "knowledge", now=HOP_NOW)
        await session.execute(
            text(
                "UPDATE public.user_preferences "
                "SET timezone = 'Asia/Shanghai' WHERE user_id = :user_id"
            ),
            {"user_id": owner},
        )
        second = await consume_quota(session, owner, "knowledge", now=HOP_NOW)
        row = await _usage(session, owner, "knowledge")
        dates = await session.scalar(
            text(
                "SELECT count(*) FROM public.daily_usage "
                "WHERE user_id = :user_id AND capability = 'knowledge'"
            ),
            {"user_id": owner},
        )
    naive_shanghai = local_usage_date(HOP_NOW, "Asia/Shanghai")
    naive_la = local_usage_date(HOP_NOW, "America/Los_Angeles")
    assert naive_shanghai != naive_la
    assert first.usage_date == naive_la
    assert second.usage_date == naive_la
    assert second.used == 2
    assert row is not None
    assert row.usage_date == naive_la
    assert str(row.timezone) == "America/Los_Angeles"
    assert int(dates or 0) == 1


async def _assert_hold_releases_on_provider_failure(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    source_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        try:
            async with hold(
                session,
                owner,
                "search",
                source_id=source_id,
                now=NOW,
                timezone="Asia/Shanghai",
            ):
                held = await _usage(session, owner, "search")
                assert held is not None
                assert int(held.reserved) == 1
                assert int(held.used) == 0
                raise RuntimeError("provider")
        except RuntimeError as exc:
            assert str(exc) == "provider"
        released = await _usage(session, owner, "search")
        status = await session.scalar(
            text(
                "SELECT status FROM private.quota_reservations "
                "WHERE user_id = :user_id AND source_id = :source_id"
            ),
            {"user_id": owner, "source_id": source_id},
        )
    assert released is not None
    assert int(released.reserved) == 0
    assert int(released.used) == 0
    assert str(status) == "released"


async def _assert_expire_due_reservations(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    source_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        held = await reserve(
            session,
            owner,
            "asr",
            source_id=source_id,
            now=NOW,
            timezone="Asia/Shanghai",
        )
        row = await _usage(session, owner, "asr")
    assert held.status == "held"
    assert row is not None
    assert int(row.reserved) == 1
    assert int(row.used) == 0

    expired = await expire_due_reservations(factory, now=NOW + RESERVATION_TTL)
    assert expired == 1
    async with claimed_transaction(factory, user) as session:
        after = await _usage(session, owner, "asr")
        status = await session.scalar(
            text(
                "SELECT status FROM private.quota_reservations "
                "WHERE user_id = :user_id AND source_id = :source_id"
            ),
            {"user_id": owner, "source_id": source_id},
        )
    assert after is not None
    assert int(after.reserved) == 0
    assert int(after.used) == 0
    assert str(status) == "released"


async def _assert_stable_429_details(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        first = await consume_quota(
            session,
            owner,
            "food",
            now=NOW,
            timezone="Asia/Shanghai",
            limit_value=1,
        )
        try:
            await consume_quota(
                session,
                owner,
                "food",
                now=NOW,
                timezone="Asia/Shanghai",
                limit_value=1,
            )
        except ApiError as exc:
            assert exc.code == "QUOTA_EXCEEDED"
            assert exc.status_code == 429
            assert exc.retryable is False
            assert exc.details == {
                "quota": "food",
                "reset_at": quota_usage(first).reset_at,
            }
        else:
            raise AssertionError("second food consume must exceed quota")
        row = await _usage(session, owner, "food")
    assert row is not None
    assert int(row.used) == 1
    assert int(row.limit_value) == 1


async def _assert_reserve_is_idempotent(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    source_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        first = await reserve(
            session,
            owner,
            "tts",
            source_id=source_id,
            now=NOW,
            timezone="Asia/Shanghai",
        )
        second = await reserve(
            session,
            owner,
            "tts",
            source_id=source_id,
            now=NOW,
            timezone="Asia/Shanghai",
        )
        row = await _usage(session, owner, "tts")
    assert first.id == second.id
    assert second.status == "held"
    assert row is not None
    assert int(row.reserved) == 1
    assert int(row.used) == 0
