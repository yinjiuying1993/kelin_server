"""Claim rating jobs and settle them. No match or answer model is called."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import claimed_transaction
from app.domain.craft_ledger.settle_outbox import CRAFT_RATING_SETTLE_EVENT
from app.domain.outbox import DEFAULT_OUTBOX_LEASE_SECONDS
from app.domain.spirit_state import require_aware
from app.repositories import outbox as outbox_repo
from app.repositories.craft_settle import SqlSettlementBook
from app.services.craft_ledger.settle import SettlementAlreadyRecorded, settle_dialogue

_WORKER_ROLE = "kelin_worker"


async def process_due_rating_settlements(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int = 20,
) -> tuple[UUID, ...]:
    require_aware(now, field="now")
    worker_id = f"craft-settle:{uuid4()}"
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.reclaim_expired_outbox(session, now=now)
        jobs = await outbox_repo.claim_due_outbox(
            session,
            now=now,
            worker_id=worker_id,
            batch=limit,
            lease_seconds=DEFAULT_OUTBOX_LEASE_SECONDS,
            event_types=(CRAFT_RATING_SETTLE_EVENT,),
        )
    done: list[UUID] = []
    for job in jobs:
        try:
            question_id = UUID(str(job.payload["ask_question_id"]))
            spirit_id = UUID(str(job.payload["spirit_id"]))
            points = int(job.payload["points"])
            skill_slug = str(job.payload["skill_slug"])
        except (KeyError, TypeError, ValueError):
            await _fail(factory, job.id, worker_id, now)
            continue
        try:
            async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
                book = SqlSettlementBook(session)
                await settle_dialogue(
                    book,
                    book,
                    book,
                    question_id=question_id,
                    spirit_id=spirit_id,
                    skill_slug=skill_slug,
                    points=points,
                )
                await outbox_repo.complete_outbox_job(
                    session,
                    job_id=job.id,
                    locked_by=worker_id,
                    now=now,
                )
        except SettlementAlreadyRecorded:
            await _complete(factory, job.id, worker_id, now)
        except Exception:
            await _fail(factory, job.id, worker_id, now)
            continue
        done.append(job.id)
    return tuple(done)


async def _complete(
    factory: async_sessionmaker[AsyncSession], job_id: UUID, worker_id: str, now: datetime
) -> None:
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.complete_outbox_job(
            session,
            job_id=job_id,
            locked_by=worker_id,
            now=now,
        )


async def _fail(
    factory: async_sessionmaker[AsyncSession], job_id: UUID, worker_id: str, now: datetime
) -> None:
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.fail_outbox_job(
            session,
            job_id=job_id,
            locked_by=worker_id,
            now=now,
            error="rating settlement unavailable",
        )
