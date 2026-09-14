"""Independent visit.settle worker. Spec §§8.9, 16.2, 17.1–17.2.

Each visit is claimed with a lease and settled in its own transaction.
Provider runs after claim with no business-row lock. Public-only postcard
input is assembled from the catalog function; failure uses the template.
Repeat settle is a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import claimed_transaction
from app.domain.spirit_state import require_aware
from app.domain.visits import DEFAULT_OUTBOX_LEASE_SECONDS
from app.providers.postcard import (
    PostcardProvider,
    build_postcard_provider,
    postcard_input_from_public,
    resolve_postcard_text,
)
from app.repositories import visits as visit_repo
from app.repositories.visits import VisitPostcardPublicInput
from app.schemas.spirit import SpiritStage

_STAGES: frozenset[str] = frozenset({"whelp", "formed", "awake"})

_SCHEDULER_ROLE = "kelin_scheduler"
_WORKER_ROLE = "kelin_worker"


@dataclass(frozen=True, slots=True)
class VisitSettleScanResult:
    inserted: int
    reclaimed: int


@dataclass(frozen=True, slots=True)
class VisitSettleTick:
    visit_id: UUID
    job_id: UUID
    changed: bool
    outcome: str


async def enqueue_due_visit_settle_jobs(
    session: AsyncSession,
    *,
    now: datetime,
) -> VisitSettleScanResult:
    require_aware(now, field="now")
    await visit_repo.assert_writable_transaction(session)
    inserted = await visit_repo.enqueue_due_visit_settle_jobs(session, now=now)
    return VisitSettleScanResult(inserted=inserted, reclaimed=0)


async def scan_due_visit_settle_jobs(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> VisitSettleScanResult:
    async with claimed_transaction(factory, None, db_role=_SCHEDULER_ROLE) as session:
        return await enqueue_due_visit_settle_jobs(session, now=now)


async def handle_visit_settle(
    factory: async_sessionmaker[AsyncSession],
    *,
    visit_id: UUID,
    now: datetime,
) -> int:
    require_aware(now, field="now")
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await visit_repo.assert_writable_transaction(session)
        return await visit_repo.settle_visit(session, visit_id=visit_id, now=now)


async def process_due_visit_settles(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int = 50,
    worker_id: str | None = None,
    settings: Settings | None = None,
    postcard_provider: PostcardProvider | None = None,
) -> tuple[VisitSettleTick, ...]:
    require_aware(now, field="now")
    resolved = settings or get_settings()
    lease = resolved.outbox_lease_seconds or DEFAULT_OUTBOX_LEASE_SECONDS
    locked_by = worker_id or str(uuid4())
    cards = (
        postcard_provider
        if postcard_provider is not None
        else build_postcard_provider(resolved)
    )
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await visit_repo.reclaim_expired_outbox(session, now=now)
        await visit_repo.sync_dead_visit_settles(session, now=now)
        jobs = await visit_repo.claim_due_visit_settle_jobs(
            session,
            now=now,
            worker_id=locked_by,
            limit=limit,
            lease_seconds=lease,
        )
    ticks: list[VisitSettleTick] = []
    for job in jobs:
        try:
            async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
                public = await visit_repo.load_visit_postcard_public_input(
                    session, visit_id=job.visit_id
                )
            visitor_text, host_text = await compose_visit_postcards(cards, public)
            async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
                changed = await visit_repo.settle_visit(
                    session,
                    visit_id=job.visit_id,
                    now=now,
                    visitor_text=visitor_text,
                    host_text=host_text,
                )
            ticks.append(
                VisitSettleTick(
                    visit_id=job.visit_id,
                    job_id=job.id,
                    changed=changed > 0,
                    outcome="done",
                )
            )
        except DBAPIError as exc:
            error_code = _sqlstate(exc) or "VISIT_SETTLE_RETRY"
            async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
                status = await visit_repo.mark_visit_settle_retry(
                    session,
                    job_id=job.id,
                    worker_id=locked_by,
                    now=now,
                    error_code=error_code,
                )
                if status == "dead":
                    await visit_repo.sync_dead_visit_settles(session, now=now)
            ticks.append(
                VisitSettleTick(
                    visit_id=job.visit_id,
                    job_id=job.id,
                    changed=False,
                    outcome=status or "retry",
                )
            )
    return tuple(ticks)


async def compose_visit_postcards(
    provider: PostcardProvider | None,
    public: VisitPostcardPublicInput | None,
) -> tuple[str, str]:
    if public is None:
        return "", ""
    dest_stage = _stage(public.dest_stage)
    visitor_stage = _stage(public.visitor_stage)
    visitor_text, _source = await resolve_postcard_text(
        provider,
        postcard_input_from_public(
            role="visitor",
            title=public.dest_title,
            stage=dest_stage,
            weather=public.dest_weather,
            marks=list(public.dest_marks),
            counterpart_title=public.visitor_title,
            counterpart_stage=visitor_stage,
            counterpart_weather=public.visitor_weather,
            counterpart_marks=list(public.visitor_marks),
            npc_id=public.npc_id,
        ),
    )
    if public.npc_id is not None or public.host_id is None:
        return visitor_text, ""
    host_text, _host_source = await resolve_postcard_text(
        provider,
        postcard_input_from_public(
            role="host",
            title=public.visitor_title,
            stage=visitor_stage,
            weather=public.visitor_weather,
            marks=list(public.visitor_marks),
            counterpart_title=public.dest_title,
            counterpart_stage=dest_stage,
            counterpart_weather=public.dest_weather,
            counterpart_marks=list(public.dest_marks),
        ),
    )
    return visitor_text, host_text


def _stage(value: str) -> SpiritStage:
    if value in _STAGES:
        return value  # type: ignore[return-value]
    return "whelp"


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    state = getattr(orig, "sqlstate", None)
    if state:
        return str(state)
    wrapped = getattr(orig, "__cause__", None)
    state = getattr(wrapped, "sqlstate", None)
    return str(state) if state else None
