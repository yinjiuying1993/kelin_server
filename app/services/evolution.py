"""Apply formed/awake and scholar marks once. Spec §8.5.

Caller must already hold spirits FOR UPDATE. This module never performs
network I/O. Pact HTTP stays P15; this is the stable mark path it will call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.evolution import (
    EvolutionSummaryPatch,
    StageAdvance,
    evolution_summary_patch,
    next_stage,
    pact_scholar_mark_key,
)
from app.repositories import evolution as evolution_repo


@dataclass(frozen=True, slots=True)
class EvolutionRecord:
    advances: tuple[StageAdvance, ...]
    summary: EvolutionSummaryPatch | None


async def maybe_advance_stage(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    now: datetime,
) -> EvolutionRecord:
    del now
    await evolution_repo.assert_writable_transaction(session)
    advances: list[StageAdvance] = []
    summary: EvolutionSummaryPatch | None = None
    for _ in range(2):
        facts = await evolution_repo.fetch_evolution_facts(
            session, owner_id=owner_id, spirit_id=spirit_id
        )
        if facts is None:
            return EvolutionRecord(advances=(), summary=None)
        nxt = next_stage(facts)
        summary = evolution_summary_patch(facts)
        if nxt is None:
            break
        row = await evolution_repo.advance_stage(
            session,
            owner_id=owner_id,
            spirit_id=spirit_id,
            from_stage=facts.stage,
            to_stage=nxt,
        )
        if row is None:
            break
        advances.append(StageAdvance(from_stage=facts.stage, to_stage=row.stage))
        summary = evolution_summary_patch(facts, stage=row.stage)
    return EvolutionRecord(advances=tuple(advances), summary=summary)


async def grant_pact_scholar_mark(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    theme: str,
    question_bank_version: str,
) -> tuple[str, ...] | None:
    await evolution_repo.assert_writable_transaction(session)
    key = pact_scholar_mark_key(theme=theme, question_bank_version=question_bank_version)
    row = await evolution_repo.append_scholar_mark(
        session, owner_id=owner_id, spirit_id=spirit_id, mark=key
    )
    if row is None:
        facts = await evolution_repo.fetch_evolution_facts(
            session, owner_id=owner_id, spirit_id=spirit_id
        )
        if facts is None:
            return None
        return facts.scholar_marks
    return row.scholar_marks
