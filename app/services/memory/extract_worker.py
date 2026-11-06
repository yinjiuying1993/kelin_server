"""Async extract worker. Model calls stay outside the chat transaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.extract import (
    ExtractSourceTurn,
    admit_extract_drafts,
    claim_still_held,
    extract_output_hash,
)
from app.domain.memory.late_extract import (
    captured_snapshot_version,
    control_keeps_row,
    extract_write_summaries,
    late_extract_decision,
)
from app.domain.outbox import DEFAULT_OUTBOX_LEASE_SECONDS
from app.domain.spirit_state import require_aware
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.extract_schema import require_extract_output
from app.providers.factory import build_provider
from app.providers.protocol import BailianProvider
from app.providers.types import ExtractInput, ExtractOutput
from app.repositories import extract as extract_repo
from app.repositories import outbox as outbox_repo
from app.services.memory.ingest_service import (
    apply_memory_decision,
    load_memory_index,
    load_recent_user_messages,
    load_topic_masks,
    sync_topic_masks,
)
from app.services.memory.window_service import EXTRACT_EVENT

_WORKER_ROLE = "kelin_worker"


@dataclass(frozen=True, slots=True)
class ExtractTick:
    window_id: UUID
    job_id: UUID
    status: str
    outcome: str


async def process_due_extracts(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int = 20,
    provider: BailianProvider | None = None,
    settings: Settings | None = None,
) -> tuple[ExtractTick, ...]:
    require_aware(now, field="now")
    resolved = settings or get_settings()
    worker_id = f"extract:{uuid4()}"
    adapter = provider if provider is not None else build_provider(resolved)
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.reclaim_expired_outbox(session, now=now)
        jobs = await outbox_repo.claim_due_outbox(
            session,
            now=now,
            worker_id=worker_id,
            batch=limit,
            lease_seconds=resolved.outbox_lease_seconds or DEFAULT_OUTBOX_LEASE_SECONDS,
            event_types=(EXTRACT_EVENT,),
        )
    ticks: list[ExtractTick] = []
    for job in jobs:
        ticks.append(
            await _run_job(
                factory,
                job_id=job.id,
                owner_id=job.owner_id,
                window_id=job.aggregate_id,
                locked_by=job.locked_by,
                captured_snapshot=captured_snapshot_version(job.payload),
                now=now,
                adapter=adapter,
            )
        )
    return tuple(ticks)


async def _run_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: UUID,
    owner_id: UUID | None,
    window_id: UUID,
    locked_by: str,
    captured_snapshot: int | None,
    now: datetime,
    adapter: BailianProvider,
) -> ExtractTick:
    if owner_id is None:
        await _complete_job(factory, job_id=job_id, locked_by=locked_by, now=now)
        return ExtractTick(window_id=window_id, job_id=job_id, status="failed", outcome="missing")
    user = CurrentUser(id=owner_id)
    claimed = await _claim_window(factory, user, window_id=window_id)
    if claimed is None:
        await _complete_job(factory, job_id=job_id, locked_by=locked_by, now=now)
        return ExtractTick(
            window_id=window_id, job_id=job_id, status="extracted", outcome="replay"
        )
    if claimed == "busy":
        await _fail_job(factory, job_id=job_id, locked_by=locked_by, now=now, error="in_progress")
        return ExtractTick(
            window_id=window_id, job_id=job_id, status="extracting", outcome="in_progress"
        )
    spirit_id, turns = claimed
    try:
        raw = await adapter.extract(
            ExtractInput(
                conversation_window_id=window_id,
                turns=extract_repo.turns_for_provider(turns),
            )
        )
        output = require_extract_output(raw)
    except (ProviderCancelled, ProviderError) as exc:
        await _mark_failed(factory, user, spirit_id=spirit_id, window_id=window_id)
        await _fail_job(
            factory,
            job_id=job_id,
            locked_by=locked_by,
            now=now,
            error=getattr(exc, "code", "MODEL_UNAVAILABLE"),
        )
        return ExtractTick(window_id=window_id, job_id=job_id, status="failed", outcome="error")
    outcome = await _commit_extract(
        factory,
        user,
        spirit_id=spirit_id,
        window_id=window_id,
        output=output,
        turns=turns,
        captured_snapshot=captured_snapshot,
        now=now,
    )
    await _complete_job(factory, job_id=job_id, locked_by=locked_by, now=now)
    return ExtractTick(window_id=window_id, job_id=job_id, status="extracted", outcome=outcome)


async def _claim_window(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    window_id: UUID,
) -> tuple[UUID, list[extract_repo.WindowTurn]] | str | None:
    async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
        spirit_id = await _owned_spirit_id(session, user.id)
        if spirit_id is None:
            return None
        window = await extract_repo.fetch_window_for_update(
            session, spirit_id=spirit_id, window_id=window_id
        )
        if window is None:
            return None
        if window.status == "extracted":
            return None
        if window.status == "extracting" and window.extract_client_id not in {None, window_id}:
            return "busy"
        if window.status not in {"ready", "failed", "extracting"}:
            return None
        claimed = await extract_repo.claim_extracting(
            session,
            spirit_id=spirit_id,
            window_id=window_id,
            client_id=window_id,
        )
        if not claimed and window.status != "extracting":
            return "busy"
        turns = await extract_repo.fetch_window_turns(
            session, spirit_id=spirit_id, window_id=window_id
        )
        return spirit_id, turns


async def _owned_spirit_id(session: AsyncSession, owner_id: UUID) -> UUID | None:
    value = await session.scalar(
        text("SELECT id FROM public.spirits WHERE user_id = :user_id"),
        {"user_id": owner_id},
    )
    if value is None:
        return None
    return UUID(str(value))


async def _mark_failed(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    spirit_id: UUID,
    window_id: UUID,
) -> None:
    async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
        await session.execute(
            text(
                "UPDATE public.conversation_windows "
                "SET status = 'failed', last_error_code = 'MODEL_UNAVAILABLE' "
                "WHERE id = :window_id AND spirit_id = :spirit_id "
                "AND status = 'extracting'"
            ),
            {"window_id": window_id, "spirit_id": spirit_id},
        )


async def _commit_extract(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    *,
    spirit_id: UUID,
    window_id: UUID,
    output: ExtractOutput,
    turns: list[extract_repo.WindowTurn],
    captured_snapshot: int | None,
    now: datetime,
) -> str:
    outcome = "missing"
    async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
        current = await session.scalar(
            text(
                "SELECT snapshot_version FROM public.spirits "
                "WHERE id = :spirit_id AND user_id = :user_id "
                "FOR UPDATE"
            ),
            {"spirit_id": spirit_id, "user_id": user.id},
        )
        if current is None:
            return "missing"
        window = await extract_repo.fetch_window_for_update(
            session, spirit_id=spirit_id, window_id=window_id
        )
        if window is None or not claim_still_held(window.status, window.extract_client_id, window_id):
            return "replay" if window is not None and window.status == "extracted" else "missing"
        decision = late_extract_decision(
            captured_snapshot_version=captured_snapshot,
            current_snapshot_version=int(current),
        )
        controlled = frozenset[str]()
        if decision == "commit":
            controlled = await _controlled_summaries(session, spirit_id=spirit_id)
        source_turns = tuple(
            ExtractSourceTurn(id=row.id, role=row.role, content=row.content) for row in turns
        )
        index = await load_memory_index(session, spirit_id)
        drafts = admit_extract_drafts(
            list(output.memories),
            source_turns,
            now=now,
            window_id=window_id,
            index=index,
            stored_masks=await load_topic_masks(session, spirit_id),
            prior_messages=await load_recent_user_messages(session, spirit_id, now=now),
        )
        await sync_topic_masks(
            session, spirit_id, source_turns, list(output.memories), now=now
        )
        output_hash = extract_output_hash([item.draft for item in drafts])
        allowed = set(
            extract_write_summaries(
                decision=decision,
                summaries=tuple(item.draft.summary for item in drafts),
                controlled_summaries=controlled,
            )
        )
        for item in drafts:
            if item.draft.summary.strip() not in allowed:
                continue
            await apply_memory_decision(
                session,
                spirit_id=spirit_id,
                decision=item.decision,
                now=now,
                tags=list(item.draft.tags),
                source_message_id=item.source_message_id,
            )
        await extract_repo.mark_extracted(
            session,
            spirit_id=spirit_id,
            window_id=window_id,
            output_hash=output_hash,
            now=now,
        )
        outcome = "discarded" if decision == "discard" else "ok"
    return outcome


async def _controlled_summaries(session: AsyncSession, *, spirit_id: UUID) -> frozenset[str]:
    rows = (
        await session.execute(
            text(
                "SELECT summary, status FROM public.memories "
                "WHERE spirit_id = :spirit_id AND status IN ('sealed', 'deleted')"
            ),
            {"spirit_id": spirit_id},
        )
    ).all()
    blocked: set[str] = set()
    for row in rows:
        status = str(row.status)
        if status == "sealed":
            kept = control_keeps_row(lifecycle="sealed")
        elif status == "deleted":
            kept = control_keeps_row(lifecycle="deleted")
        else:
            continue
        if kept:
            blocked.add(str(row.summary).strip())
    return frozenset(blocked)


async def _complete_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    job_id: UUID,
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
    job_id: UUID,
    locked_by: str,
    now: datetime,
    error: str,
) -> None:
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.fail_outbox_job(
            session, job_id=job_id, locked_by=locked_by, now=now, error=error
        )
