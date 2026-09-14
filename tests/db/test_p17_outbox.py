"""P17-T02 dual-worker claim, renew, reclaim, and fencing."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.repositories import outbox as outbox_repo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
WORKER_A = "worker-a"
WORKER_B = "worker-b"


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _insert_job(url: str, *, owner_id: uuid.UUID, aggregate_id: uuid.UUID) -> uuid.UUID:
    engine = create_async_engine(url)
    job_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.outbox_events ("
                "id, aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, "
                "payload, status, available_at, max_attempts"
                ") VALUES ("
                ":id, 'test', :aggregate_id, 'test.fence', :dedupe, :owner_id, "
                "CAST(:payload AS jsonb), 'pending', :now, 2"
                ")"
            ),
            {
                "id": job_id,
                "aggregate_id": aggregate_id,
                "dedupe": f"test-fence:{job_id}",
                "owner_id": owner_id,
                "payload": json.dumps({"type": "care"}),
                "now": NOW,
            },
        )
    await engine.dispose()
    return job_id


def test_outbox_claim_renew_reclaim_and_fence() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_outbox(url))


async def _assert_outbox(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_id = uuid.uuid4()
    await _insert_user(url, owner_id)
    job_id = await _insert_job(url, owner_id=owner_id, aggregate_id=owner_id)
    async with claimed_transaction(factory, None, db_role="kelin_worker") as session:
        first = await outbox_repo.claim_due_outbox(
            session, now=NOW, worker_id=WORKER_A, batch=8, event_types=("test.fence",)
        )
        second = await outbox_repo.claim_due_outbox(
            session, now=NOW, worker_id=WORKER_B, batch=8, event_types=("test.fence",)
        )
    assert len(first) == 1
    assert first[0].id == job_id
    assert first[0].locked_by == WORKER_A
    assert second == ()
    async with claimed_transaction(factory, None, db_role="kelin_worker") as session:
        renewed = await outbox_repo.renew_outbox_lease(
            session, job_id=job_id, locked_by=WORKER_A, now=NOW + timedelta(seconds=30)
        )
        stale = await outbox_repo.renew_outbox_lease(
            session, job_id=job_id, locked_by=WORKER_B, now=NOW + timedelta(seconds=30)
        )
    assert renewed is True
    assert stale is False
    inspect = create_async_engine(url)
    async with inspect.begin() as conn:
        await conn.execute(
            text(
                "UPDATE public.outbox_events SET lease_expires_at = :expired "
                "WHERE id = :id"
            ),
            {"expired": NOW + timedelta(seconds=31), "id": job_id},
        )
    await inspect.dispose()
    later = NOW + timedelta(seconds=40)
    async with claimed_transaction(factory, None, db_role="kelin_worker") as session:
        reclaimed = await outbox_repo.reclaim_expired_outbox(session, now=later)
    after_backoff = later + timedelta(seconds=3)
    async with claimed_transaction(factory, None, db_role="kelin_worker") as session:
        taken = await outbox_repo.claim_due_outbox(
            session,
            now=after_backoff,
            worker_id=WORKER_B,
            batch=8,
            event_types=("test.fence",),
        )
        old = await outbox_repo.complete_outbox_job(
            session, job_id=job_id, locked_by=WORKER_A, now=after_backoff
        )
        new = await outbox_repo.complete_outbox_job(
            session, job_id=job_id, locked_by=WORKER_B, now=after_backoff
        )
    assert reclaimed == 1
    assert len(taken) == 1
    assert taken[0].locked_by == WORKER_B
    assert old is False
    assert new is True
    dead_id = await _insert_job(url, owner_id=owner_id, aggregate_id=uuid.uuid4())
    async with claimed_transaction(factory, None, db_role="kelin_worker") as session:
        claimed = await outbox_repo.claim_due_outbox(
            session, now=later, worker_id=WORKER_A, batch=8, event_types=("test.fence",)
        )
        assert claimed[0].id == dead_id
        failed = await outbox_repo.fail_outbox_job(
            session,
            job_id=dead_id,
            locked_by=WORKER_A,
            now=later,
            error="TEMPORARY",
        )
    assert failed is True
    retry_at = later + timedelta(seconds=4)
    async with claimed_transaction(factory, None, db_role="kelin_worker") as session:
        retried = await outbox_repo.claim_due_outbox(
            session, now=retry_at, worker_id=WORKER_A, batch=8, event_types=("test.fence",)
        )
        assert retried[0].id == dead_id
        dead = await outbox_repo.fail_outbox_job(
            session,
            job_id=dead_id,
            locked_by=WORKER_A,
            now=retry_at,
            error="TEMPORARY",
        )
    assert dead is True
    status_engine = create_async_engine(url)
    async with status_engine.connect() as conn:
        status = await conn.scalar(
            text("SELECT status FROM public.outbox_events WHERE id = :id"),
            {"id": dead_id},
        )
    await status_engine.dispose()
    assert status == "dead"
    await engine.dispose()
