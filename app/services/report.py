"""GET /report: eligibility gate and first-time generating resource. Spec §14.7."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.domain.bootstrap import report_eligibility_values
from app.domain.spirit_state import require_aware
from app.repositories import report as report_repo
from app.schemas.report import ReportSnapshot
from app.services.report_project import (
    eligibility_model,
    eligibility_snapshot_payload,
    project_snapshot,
)


async def _live_for_row(
    session: AsyncSession,
    owner_id: UUID,
    row: report_repo.ReportRow | None,
) -> dict[UUID, report_repo.MemoryLiveRow]:
    if row is None or not row.top_memory_ids:
        return {}
    return await report_repo.load_memories_for_ids(
        session,
        owner_id=owner_id,
        memory_ids=row.top_memory_ids,
    )


async def load_report_snapshot(
    session: AsyncSession,
    user: CurrentUser,
    *,
    now: datetime,
) -> ReportSnapshot:
    require_aware(now, field="now")
    await report_repo.assert_writable_transaction(session)
    locked = await report_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        return project_snapshot(
            hatched_at=None,
            ordinary_dialogue_rounds=0,
            now=now,
            row=None,
        )
    eligibility = report_eligibility_values(
        hatched_at=locked.hatched_at,
        ordinary_dialogue_rounds=locked.ordinary_dialogue_rounds,
        now=now,
    )
    existing = await report_repo.fetch_owned_report(session, user.id, for_update=True)
    if existing is None and eligibility.is_eligible:
        created = await report_repo.insert_generating_report(
            session,
            spirit_id=locked.id,
            eligibility_snapshot=eligibility_snapshot_payload(
                eligibility_model(
                    hatched_at=locked.hatched_at,
                    ordinary_dialogue_rounds=locked.ordinary_dialogue_rounds,
                    now=now,
                )
            ),
        )
        if created is not None:
            await report_repo.insert_generate_outbox(
                session,
                owner_id=user.id,
                report_id=created,
                spirit_id=locked.id,
            )
            await report_repo.bump_spirit_version(
                session, owner_id=user.id, spirit_id=locked.id
            )
        existing = await report_repo.fetch_owned_report(session, user.id, for_update=True)
    elif existing is not None and existing.status == "generating":
        await report_repo.insert_generate_outbox(
            session,
            owner_id=user.id,
            report_id=existing.id,
            spirit_id=locked.id,
        )
    live = await _live_for_row(session, user.id, existing)
    return project_snapshot(
        hatched_at=locked.hatched_at,
        ordinary_dialogue_rounds=locked.ordinary_dialogue_rounds,
        now=now,
        row=existing,
        live_memories=live,
    )


async def load_report_snapshot_readonly(
    session: AsyncSession,
    user: CurrentUser,
    *,
    now: datetime,
    hatched_at: datetime | None,
    ordinary_dialogue_rounds: int,
    report_id: UUID | None,
    report_status: str | None,
) -> ReportSnapshot:
    """Bootstrap projection. Does not insert or bump."""

    require_aware(now, field="now")
    row = None
    if report_id is not None and report_status is not None:
        row = await report_repo.fetch_owned_report(session, user.id, for_update=False)
    live = await _live_for_row(session, user.id, row)
    return project_snapshot(
        hatched_at=hatched_at,
        ordinary_dialogue_rounds=ordinary_dialogue_rounds,
        now=now,
        row=row,
        live_memories=live,
    )
