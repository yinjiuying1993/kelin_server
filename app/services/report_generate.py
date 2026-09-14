"""report.generate worker. Spec §§8.10, 14.7, 17.1.

Stable title/traits/weather/marks/memory ids are written once. Line failure
leaves partial. Retry of a finished card is a no-op.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.feed import room_weather_for_emotion
from app.domain.outbox import DEFAULT_OUTBOX_LEASE_SECONDS
from app.domain.report import (
    REPORT_GENERATE_EVENT,
    public_layers_for_report,
    title_from_traits,
    top_traits_from_values,
)
from app.domain.spirit_state import require_aware
from app.providers.errors import ProviderCancelled, ProviderError
from app.providers.report_line import (
    ReportLineInput,
    ReportLineProvider,
    build_report_line_provider,
)
from app.repositories import outbox as outbox_repo
from app.repositories import report as report_repo
from app.repositories.report import SpiritLock

_WORKER_ROLE = "kelin_worker"


@dataclass(frozen=True, slots=True)
class ReportGenerateTick:
    report_id: UUID
    job_id: UUID
    status: str
    outcome: str


def _sqlstate(exc: DBAPIError) -> str | None:
    orig = getattr(exc, "orig", None)
    state = getattr(orig, "sqlstate", None)
    if state:
        return str(state)
    wrapped = getattr(orig, "__cause__", None)
    state = getattr(wrapped, "sqlstate", None)
    return str(state) if state else None


def _line_input(locked: SpiritLock, *, title: str, weather: str) -> ReportLineInput:
    traits = top_traits_from_values(
        {
            "closeness": locked.closeness,
            "curiosity": locked.curiosity,
            "sharpness": locked.sharpness,
            "nocturnal": locked.nocturnal,
            "stubborn": locked.stubborn,
        }
    )
    return ReportLineInput(
        title=title,
        weather=weather,
        traits=[str(item["dimension"]) for item in traits],
        marks=list(locked.scholar_marks),
    )


async def _assemble_card(
    session: AsyncSession,
    *,
    owner_id: UUID,
    locked: SpiritLock,
    now: datetime,
) -> tuple[str, dict[str, object], str, list[dict[str, object]], tuple[UUID, ...], list[dict[str, object]], tuple[str, ...], str]:
    title = title_from_traits(
        closeness=locked.closeness,
        sharpness=locked.sharpness,
        nocturnal=locked.nocturnal,
        scholar_marks=locked.scholar_marks,
    )
    weather = room_weather_for_emotion(
        await report_repo.latest_emotion(session, owner_id=owner_id)
    )
    traits = [
        {"dimension": str(item["dimension"]), "value": int(item["value"])}
        for item in top_traits_from_values(
            {
                "closeness": locked.closeness,
                "curiosity": locked.curiosity,
                "sharpness": locked.sharpness,
                "nocturnal": locked.nocturnal,
                "stubborn": locked.stubborn,
            }
        )
    ]
    memories = await report_repo.select_top_active_memories(
        session, owner_id=owner_id, spirit_id=locked.id
    )
    memory_ids = tuple(item.id for item in memories)
    memory_snapshot = [
        {"id": str(item.id), "type": item.type, "summary": item.summary}
        for item in memories
    ]
    spirit_snapshot = {
        "id": str(locked.id),
        "name": locked.name,
        "stage": locked.stage,
        "public_layers": list(
            public_layers_for_report(
                status=locked.status, scholar_marks=locked.scholar_marks
            )
        ),
    }
    return (
        title,
        spirit_snapshot,
        weather,
        traits,
        memory_ids,
        memory_snapshot,
        locked.scholar_marks,
        locked.invite_code,
    )


async def process_due_report_generates(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int = 20,
    settings: Settings | None = None,
    line_provider: ReportLineProvider | None = None,
) -> tuple[ReportGenerateTick, ...]:
    require_aware(now, field="now")
    resolved = settings or get_settings()
    worker_id = f"report-generate:{uuid4()}"
    provider = line_provider or build_report_line_provider(resolved)
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.reclaim_expired_outbox(session, now=now)
        jobs = await outbox_repo.claim_due_outbox(
            session,
            now=now,
            worker_id=worker_id,
            batch=limit,
            lease_seconds=resolved.outbox_lease_seconds or DEFAULT_OUTBOX_LEASE_SECONDS,
            event_types=(REPORT_GENERATE_EVENT,),
        )
    ticks: list[ReportGenerateTick] = []
    for job in jobs:
        if job.owner_id is None:
            continue
        user = CurrentUser(id=job.owner_id)
        try:
            async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
                row = await report_repo.fetch_owned_report(session, user.id, for_update=True)
                locked = await report_repo.lock_owned_spirit(session, user.id)
            if row is None or locked is None:
                async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
                    await outbox_repo.complete_outbox_job(
                        session, job_id=job.id, locked_by=job.locked_by, now=now
                    )
                ticks.append(
                    ReportGenerateTick(
                        report_id=job.aggregate_id,
                        job_id=job.id,
                        status="failed",
                        outcome="missing",
                    )
                )
                continue
            if row.status in {"ready", "partial", "failed"}:
                async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
                    await outbox_repo.complete_outbox_job(
                        session, job_id=job.id, locked_by=job.locked_by, now=now
                    )
                ticks.append(
                    ReportGenerateTick(
                        report_id=row.id,
                        job_id=job.id,
                        status=row.status,
                        outcome="noop",
                    )
                )
                continue
            async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
                locked = await report_repo.lock_owned_spirit(session, user.id)
                row = await report_repo.fetch_owned_report(session, user.id, for_update=True)
                if locked is None or row is None:
                    raise RuntimeError("report generate lost owner row")
                assembled = await _assemble_card(
                    session, owner_id=user.id, locked=locked, now=now
                )
                expected_version = row.version
                spirit_id = locked.id
            (
                title,
                spirit_snapshot,
                weather,
                traits,
                memory_ids,
                memory_snapshot,
                scholar_marks,
                invite_code,
            ) = assembled
            signature_line: str | None = None
            status = "partial"
            try:
                output = await provider.generate(
                    _line_input(locked, title=title, weather=weather)
                )
                text = output.text.strip()
                if text:
                    signature_line = text
                    status = "ready"
            except (ProviderError, ProviderCancelled):
                signature_line = None
                status = "partial"
            async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
                saved = await report_repo.save_generated_card(
                    session,
                    owner_id=user.id,
                    report_id=row.id,
                    expected_version=expected_version,
                    status=status,
                    title=title,
                    spirit_snapshot=spirit_snapshot,
                    room_weather=weather,
                    top_traits=traits,
                    top_memory_ids=memory_ids,
                    top_memories_snapshot=memory_snapshot,
                    scholar_marks=scholar_marks,
                    signature_line=signature_line,
                    invite_code=invite_code,
                    generated_at=now,
                )
                if saved is None:
                    current = await report_repo.fetch_owned_report(session, user.id)
                    status = current.status if current is not None else "generating"
                else:
                    await report_repo.bump_spirit_version(
                        session, owner_id=user.id, spirit_id=spirit_id
                    )
                    status = saved.status
                await outbox_repo.complete_outbox_job(
                    session, job_id=job.id, locked_by=job.locked_by, now=now
                )
            ticks.append(
                ReportGenerateTick(
                    report_id=row.id,
                    job_id=job.id,
                    status=status,
                    outcome="done",
                )
            )
        except DBAPIError as exc:
            error = _sqlstate(exc) or "REPORT_GENERATE_RETRY"
            async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
                await outbox_repo.fail_outbox_job(
                    session,
                    job_id=job.id,
                    locked_by=job.locked_by,
                    now=now,
                    error=error,
                )
            ticks.append(
                ReportGenerateTick(
                    report_id=job.aggregate_id,
                    job_id=job.id,
                    status="generating",
                    outcome="retry",
                )
            )
    return tuple(ticks)
