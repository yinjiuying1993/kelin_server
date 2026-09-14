"""Owner-scoped active recall for Prompt, citations, and report candidates.

Spec §§8.3, 12.3, 16.3. Historical extract window rows stay in extract.py;
generation must use these queries so sealed/deleted never re-enter Prompt.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory_recall import (
    PROMPT_MEMORY_LIMIT,
    PROMPT_STYLE_SAMPLE_LIMIT,
    REPORT_MEMORY_CANDIDATE_LIMIT,
)


@dataclass(frozen=True, slots=True)
class PromptMemoryRow:
    id: uuid.UUID
    type: str
    summary: str


@dataclass(frozen=True, slots=True)
class PromptStyleSampleRow:
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class PromptRecall:
    memories: tuple[PromptMemoryRow, ...]
    style_samples: tuple[PromptStyleSampleRow, ...]


@dataclass(frozen=True, slots=True)
class ReportMemoryCandidate:
    id: uuid.UUID
    type: str
    salience: int


async def fetch_active_owned_memory_ids(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    memory_ids: tuple[uuid.UUID, ...],
) -> frozenset[uuid.UUID]:
    if not memory_ids:
        return frozenset()
    statement = text(
        "SELECT m.id FROM public.memories m "
        "JOIN public.spirits s ON s.id = m.spirit_id "
        "WHERE m.spirit_id = :spirit_id AND s.user_id = :owner_id "
        "AND m.status = 'active' AND m.id IN :ids"
    ).bindparams(bindparam("ids", expanding=True))
    rows = await session.execute(
        statement,
        {"spirit_id": spirit_id, "owner_id": owner_id, "ids": list(memory_ids)},
    )
    return frozenset(row.id for row in rows)


async def fetch_prompt_memories(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
) -> tuple[PromptMemoryRow, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT m.id, m.type, m.summary FROM public.memories m "
                "JOIN public.spirits s ON s.id = m.spirit_id "
                "WHERE m.spirit_id = :spirit_id AND s.user_id = :owner_id "
                "AND m.status = 'active' "
                "ORDER BY m.created_at DESC, m.id DESC LIMIT :limit"
            ),
            {
                "spirit_id": spirit_id,
                "owner_id": owner_id,
                "limit": PROMPT_MEMORY_LIMIT,
            },
        )
    ).all()
    return tuple(
        PromptMemoryRow(id=row.id, type=str(row.type), summary=str(row.summary)) for row in rows
    )


async def fetch_prompt_style_samples(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
) -> tuple[PromptStyleSampleRow, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT ss.kind, ss.text FROM public.style_samples ss "
                "JOIN public.spirits s ON s.id = ss.spirit_id "
                "WHERE ss.spirit_id = :spirit_id AND s.user_id = :owner_id "
                "AND ss.status = 'active' "
                "ORDER BY ss.created_at ASC, ss.id ASC LIMIT :limit"
            ),
            {
                "spirit_id": spirit_id,
                "owner_id": owner_id,
                "limit": PROMPT_STYLE_SAMPLE_LIMIT,
            },
        )
    ).all()
    return tuple(PromptStyleSampleRow(kind=str(row.kind), text=str(row.text)) for row in rows)


async def load_prompt_recall(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
) -> PromptRecall:
    memories = await fetch_prompt_memories(session, owner_id=owner_id, spirit_id=spirit_id)
    samples = await fetch_prompt_style_samples(session, owner_id=owner_id, spirit_id=spirit_id)
    return PromptRecall(memories=memories, style_samples=samples)


async def fetch_report_memory_candidates(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
) -> tuple[ReportMemoryCandidate, ...]:
    """P18 report generation is unwired; this is the only candidate query it may use."""

    rows = (
        await session.execute(
            text(
                "SELECT m.id, m.type, m.salience FROM public.memories m "
                "JOIN public.spirits s ON s.id = m.spirit_id "
                "WHERE m.spirit_id = :spirit_id AND s.user_id = :owner_id "
                "AND m.status = 'active' "
                "ORDER BY m.salience DESC, m.created_at DESC, m.id DESC LIMIT :limit"
            ),
            {
                "spirit_id": spirit_id,
                "owner_id": owner_id,
                "limit": REPORT_MEMORY_CANDIDATE_LIMIT,
            },
        )
    ).all()
    return tuple(
        ReportMemoryCandidate(id=row.id, type=str(row.type), salience=int(row.salience))
        for row in rows
    )
