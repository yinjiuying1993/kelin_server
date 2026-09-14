"""Evolution persistence. Spec §8.5.

Caller must already hold spirits FOR UPDATE. Stage CAS is the once-gate.
Scholar marks append only when the stable key is absent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.evolution import (
    EvolutionFacts,
    require_forward_step,
    require_scholar_mark_key,
    unique_scholar_marks,
)

_FACTS_SQL = """
SELECT
  s.stage,
  s.bond,
  s.has_visited,
  s.has_been_lost,
  s.scholar_marks,
  ARRAY(
    SELECT DISTINCT m.type
    FROM public.memories m
    WHERE m.spirit_id = s.id AND m.status = 'active'
  ) AS memory_types,
  EXISTS (
    SELECT 1 FROM public.feeds f
    WHERE f.spirit_id = s.id
      AND f.kind = 'knowledge'
      AND f.status = 'accepted'
      AND f.effect_applied_at IS NOT NULL
  ) AS knowledge_growth,
  EXISTS (
    SELECT 1 FROM public.growth_events g
    WHERE g.spirit_id = s.id
      AND g.event_type = 'pact_completed'
      AND g.applied_at IS NOT NULL
  ) AS pact_growth
FROM public.spirits s
WHERE s.id = :id AND s.user_id = :user_id
"""


@dataclass(frozen=True, slots=True)
class StageRow:
    stage: str
    version: int


@dataclass(frozen=True, slots=True)
class ScholarMarkRow:
    scholar_marks: tuple[str, ...]
    version: int


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("evolution cannot write in a read-only transaction")


async def fetch_evolution_facts(
    session: AsyncSession, *, owner_id: uuid.UUID, spirit_id: uuid.UUID
) -> EvolutionFacts | None:
    row = (
        await session.execute(
            text(_FACTS_SQL),
            {"id": spirit_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    types = row.memory_types or []
    marks = row.scholar_marks or []
    return EvolutionFacts(
        stage=str(row.stage),
        bond=int(row.bond),
        active_memory_types=frozenset(str(item) for item in types),
        has_knowledge_or_pact_growth=bool(row.knowledge_growth or row.pact_growth),
        has_visited=bool(row.has_visited),
        has_been_lost=bool(row.has_been_lost),
        scholar_marks=unique_scholar_marks(tuple(str(item) for item in marks)),
    )


async def advance_stage(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    from_stage: str,
    to_stage: str,
) -> StageRow | None:
    require_forward_step(from_stage, to_stage)
    row = (
        await session.execute(
            text(
                "UPDATE public.spirits SET stage = :to_stage, version = version + 1 "
                "WHERE id = :id AND user_id = :user_id AND stage = :from_stage "
                "RETURNING stage, version"
            ),
            {
                "id": spirit_id,
                "user_id": owner_id,
                "from_stage": from_stage,
                "to_stage": to_stage,
            },
        )
    ).first()
    if row is None:
        return None
    return StageRow(stage=str(row.stage), version=int(row.version))


async def append_scholar_mark(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    mark: str,
) -> ScholarMarkRow | None:
    key = require_scholar_mark_key(mark)
    row = (
        await session.execute(
            text(
                "UPDATE public.spirits SET "
                "scholar_marks = scholar_marks || ARRAY[:mark]::text[], "
                "version = version + 1 "
                "WHERE id = :id AND user_id = :user_id "
                "AND NOT (scholar_marks @> ARRAY[:mark]::text[]) "
                "RETURNING scholar_marks, version"
            ),
            {"id": spirit_id, "user_id": owner_id, "mark": key},
        )
    ).first()
    if row is None:
        return None
    marks = row.scholar_marks or []
    return ScholarMarkRow(
        scholar_marks=unique_scholar_marks(tuple(str(item) for item in marks)),
        version=int(row.version),
    )
