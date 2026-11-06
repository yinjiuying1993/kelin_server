"""Per-turn memory judgment. The model decides; identical summaries are not stored."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.memory.constants import RULES_VERSION
from app.domain.memory.judge import JudgedMemory, judgment_memories
from app.domain.memory.types import MemoryDecision
from app.domain.outbox import DEFAULT_OUTBOX_LEASE_SECONDS
from app.domain.spirit_state import require_aware
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.factory import build_provider
from app.providers.protocol import BailianProvider
from app.repositories import outbox as outbox_repo
from app.services.memory.ingest_service import apply_memory_decision
from app.services.memory.window_service import TURN_EXTRACT_EVENT

_WORKER_ROLE = "kelin_worker"
_STORED_SALIENCE = 60
_STORED_CONFIDENCE = 1.0


@dataclass(frozen=True, slots=True)
class TurnJudgeTick:
    message_id: uuid.UUID
    job_id: uuid.UUID
    outcome: str


async def process_due_turn_judgments(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int = 20,
    provider: BailianProvider | None = None,
    settings: Settings | None = None,
) -> tuple[TurnJudgeTick, ...]:
    require_aware(now, field="now")
    resolved = settings or get_settings()
    worker_id = f"turn-judge:{uuid.uuid4()}"
    adapter = provider if provider is not None else build_provider(resolved)
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.reclaim_expired_outbox(session, now=now)
        jobs = await outbox_repo.claim_due_outbox(
            session,
            now=now,
            worker_id=worker_id,
            batch=limit,
            lease_seconds=resolved.outbox_lease_seconds or DEFAULT_OUTBOX_LEASE_SECONDS,
            event_types=(TURN_EXTRACT_EVENT,),
        )
    ticks: list[TurnJudgeTick] = []
    for job in jobs:
        message_id = job.aggregate_id
        if job.owner_id is None:
            await _complete_job(factory, job_id=job.id, locked_by=job.locked_by, now=now)
            ticks.append(TurnJudgeTick(message_id=message_id, job_id=job.id, outcome="missing"))
            continue
        loaded = await _load_user_message(factory, owner_id=job.owner_id, message_id=message_id)
        if loaded is None:
            await _complete_job(factory, job_id=job.id, locked_by=job.locked_by, now=now)
            ticks.append(TurnJudgeTick(message_id=message_id, job_id=job.id, outcome="missing"))
            continue
        spirit_id, content = loaded
        try:
            payload = await _judge(adapter, content)
        except (ProviderCancelled, ProviderError) as exc:
            await _fail_job(
                factory,
                job_id=job.id,
                locked_by=job.locked_by,
                now=now,
                error=getattr(exc, "code", "MODEL_UNAVAILABLE"),
            )
            ticks.append(TurnJudgeTick(message_id=message_id, job_id=job.id, outcome="error"))
            continue
        outcome = await _store_judgment(
            factory,
            owner_id=job.owner_id,
            spirit_id=spirit_id,
            message_id=message_id,
            payload=payload,
            now=now,
        )
        if outcome == "invalid":
            await _fail_job(
                factory,
                job_id=job.id,
                locked_by=job.locked_by,
                now=now,
                error="MODEL_UNAVAILABLE",
            )
        else:
            await _complete_job(factory, job_id=job.id, locked_by=job.locked_by, now=now)
        ticks.append(TurnJudgeTick(message_id=message_id, job_id=job.id, outcome=outcome))
    return tuple(ticks)


async def _judge(adapter: BailianProvider, content: str) -> object:
    judge = getattr(adapter, "judge_memory", None)
    if judge is None:
        raise ProviderError("MODEL_UNAVAILABLE")
    return await judge(content)


async def _load_user_message(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner_id: uuid.UUID,
    message_id: uuid.UUID,
) -> tuple[uuid.UUID, str] | None:
    async with claimed_transaction(factory, CurrentUser(id=owner_id), db_role=_WORKER_ROLE) as session:
        row = (
            await session.execute(
                text(
                    "SELECT m.spirit_id, m.content FROM public.messages m "
                    "JOIN public.spirits s ON s.id = m.spirit_id "
                    "WHERE m.id = :message_id AND s.user_id = :owner_id AND m.role = 'user'"
                ),
                {"message_id": message_id, "owner_id": owner_id},
            )
        ).first()
    if row is None:
        return None
    content = str(row.content).strip()
    if not content:
        return None
    return uuid.UUID(str(row.spirit_id)), content


async def _store_judgment(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: object,
    now: datetime,
) -> str:
    async with claimed_transaction(factory, CurrentUser(id=owner_id), db_role=_WORKER_ROLE) as session:
        rows = (
            await session.execute(
                text("SELECT summary FROM public.memories WHERE spirit_id = :spirit_id"),
                {"spirit_id": spirit_id},
            )
        ).all()
        existing = {str(row.summary) for row in rows}
        chosen = judgment_memories(payload, existing)
        if chosen is None:
            return "invalid"
        for item in chosen:
            await apply_memory_decision(
                session,
                spirit_id=spirit_id,
                decision=_decision(item, message_id),
                now=now,
                tags=[],
                source_message_id=message_id,
            )
    return "stored" if chosen else "empty"


def _decision(item: JudgedMemory, message_id: uuid.UUID) -> MemoryDecision:
    digest = hashlib.sha256(item.summary.encode()).hexdigest()[:12]
    return MemoryDecision(
        decision="accept",
        candidate_id=uuid.uuid4(),
        memory_type=item.memory_type,
        normalized_topic_key=f"{item.memory_type}:user:said:{digest}",
        summary=item.summary,
        confidence=_STORED_CONFIDENCE,
        salience=_STORED_SALIENCE,
        source_event_id=uuid.uuid5(message_id, item.summary),
        source_message_ids=(message_id,),
        conflict_target_id=None,
        rejection_code=None,
        confirm_delta=0,
        rules_version=RULES_VERSION,
    )


async def _complete_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    locked_by: str,
    now: datetime,
) -> None:
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.complete_outbox_job(
            session, job_id=job_id, locked_by=locked_by, now=now
        )


async def _fail_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    locked_by: str,
    now: datetime,
    error: str,
) -> None:
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.fail_outbox_job(
            session, job_id=job_id, locked_by=locked_by, now=now, error=error
        )
