from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.repositories import bootstrap as bootstrap_repo
from app.schemas.spirit import CreateSpiritRequest
from app.services.bootstrap import load_bootstrap_snapshot
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

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


def test_new_user_snapshot_is_complete_and_transaction_is_read_only() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_new_user_and_readonly(url))


async def _assert_new_user_and_readonly(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user = CurrentUser(id=uuid.uuid4())
    await _insert_auth_user(url, user.id)
    async with claimed_read_transaction(factory, user) as session:
        isolation = str(await session.scalar(text("SHOW transaction_isolation"))).lower()
        readonly = str(await session.scalar(text("SHOW transaction_read_only"))).lower()
        snapshot = await load_bootstrap_snapshot(session, user, now=NOW)
        with pytest.raises(DBAPIError):
            await session.execute(
                text("INSERT INTO public.user_preferences (user_id) VALUES (:id)"),
                {"id": user.id},
            )
    assert isolation == "repeatable read"
    assert readonly == "on"
    assert snapshot.spirit is None
    assert snapshot.room is None
    assert snapshot.snapshot_version == 0
    assert snapshot.onboarding.required is True
    assert snapshot.onboarding.step == 0
    assert snapshot.latest_memories == []
    assert snapshot.unread_postcards == []
    assert snapshot.active_pact is None
    assert snapshot.report.status == "locked"
    assert snapshot.report.report_id is None
    assert snapshot.report.card is None
    assert snapshot.quotas == []
    assert snapshot.feature_flags.remote_search is True
    assert snapshot.feature_flags.image_feed is True
    await engine.dispose()


def test_owner_snapshot_sees_latest_committed_version_not_other_account() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_owner_isolation_and_latest_version(url))


async def _assert_owner_isolation_and_latest_version(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_a = CurrentUser(id=uuid.uuid4())
    user_b = CurrentUser(id=uuid.uuid4())
    await _insert_auth_user(url, user_a.id)
    await _insert_auth_user(url, user_b.id)

    async with claimed_transaction(factory, user_a) as session:
        created_a = await create_spirit_if_absent(session, user_a, _request())
        await session.execute(
            text("UPDATE public.spirits SET last_interact_at = :ts WHERE user_id = :user_id"),
            {"ts": NOW - timedelta(hours=1), "user_id": user_a.id},
        )
    async with claimed_transaction(factory, user_b) as session:
        created_b = await create_spirit_if_absent(session, user_b, _request())
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'preference', 'B记忆', 80, 0.9)"
            ),
            {"id": uuid.uuid4(), "spirit_id": created_b.spirit_id},
        )
        await session.execute(
            text(
                "INSERT INTO public.feeds ("
                "user_id, spirit_id, client_id, kind, payload, status"
                ") VALUES ("
                ":user_id, :spirit_id, :client_id, 'sight', "
                "CAST(:payload AS jsonb), 'pending')"
            ),
            {
                "user_id": user_b.id,
                "spirit_id": created_b.spirit_id,
                "client_id": uuid.uuid4(),
                "payload": json.dumps({"source": "photo"}),
            },
        )
        memory_b = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'knowledge', 'B知识', 40, 0.8)"
            ),
            {"id": memory_b, "spirit_id": created_b.spirit_id},
        )

    async with claimed_transaction(factory, user_a) as session:
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'preference', 'A记忆', 80, 0.9)"
            ),
            {"id": uuid.uuid4(), "spirit_id": created_a.spirit_id},
        )
        await session.execute(
            text(
                "INSERT INTO public.feeds ("
                "user_id, spirit_id, client_id, kind, payload, status"
                ") VALUES ("
                ":user_id, :spirit_id, :client_id, 'sight', "
                "CAST(:payload AS jsonb), 'pending')"
            ),
            {
                "user_id": user_a.id,
                "spirit_id": created_a.spirit_id,
                "client_id": uuid.uuid4(),
                "payload": json.dumps({"source": "photo"}),
            },
        )

    async with claimed_read_transaction(factory, user_a) as session:
        before = await load_bootstrap_snapshot(session, user_a, now=NOW)
        rows_before = await bootstrap_repo.load_bootstrap_aggregate_rows(
            session, user_a.id, now=NOW
        )
    assert before.snapshot_version == 1
    assert before.spirit is not None
    assert before.room is not None
    assert before.spirit.id == created_a.spirit_id
    assert before.room.pending_sight is not None
    assert before.room.unread_footprint_count == 0
    assert before.latest_memories == []
    assert before.active_pact is None
    assert before.report.status == "locked"
    assert str(created_b.spirit_id) not in before.model_dump_json()
    assert str(memory_b) not in before.model_dump_json()
    assert memory_b not in rows_before.memory_ids
    assert created_a.spirit_id != created_b.spirit_id

    async with claimed_transaction(factory, user_a) as session:
        await session.execute(
            text(
                "UPDATE public.spirits SET name = '雾生', version = version + 1 "
                "WHERE user_id = :user_id"
            ),
            {"user_id": user_a.id},
        )

    async with claimed_read_transaction(factory, user_a) as session:
        after = await load_bootstrap_snapshot(session, user_a, now=NOW)
        rows_after = await bootstrap_repo.load_bootstrap_aggregate_rows(session, user_a.id, now=NOW)
    assert after.snapshot_version == 2
    assert after.spirit is not None
    assert after.spirit.version == 2
    assert after.spirit.name == "雾生"
    assert after.spirit.id == created_a.spirit_id
    assert str(created_b.spirit_id) not in after.model_dump_json()
    assert memory_b not in rows_after.memory_ids
    assert after.room is not None
    assert after.room.pending_sight is not None
    assert after.room.pending_sight.source == "photo"

    async with claimed_read_transaction(factory, user_b) as session:
        snapshot_b = await load_bootstrap_snapshot(session, user_b, now=NOW)
        rows_b = await bootstrap_repo.load_bootstrap_aggregate_rows(session, user_b.id, now=NOW)
    assert snapshot_b.spirit is not None
    assert snapshot_b.spirit.id == created_b.spirit_id
    assert snapshot_b.snapshot_version == 1
    assert memory_b in rows_b.memory_ids
    assert str(created_a.spirit_id) not in snapshot_b.model_dump_json()
    await engine.dispose()
