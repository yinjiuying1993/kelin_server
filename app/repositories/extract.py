"""Owner-filtered extract persistence. Spec §§8.3, 10.3."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.extract import MemoryRecord, StyleSampleRecord
from app.providers.types import ExtractTurn, TraitDimension

TRAIT_COLUMNS: tuple[TraitDimension, ...] = (
    "closeness",
    "curiosity",
    "sharpness",
    "nocturnal",
    "stubborn",
)


@dataclass(frozen=True, slots=True)
class ExtractWindowRow:
    id: uuid.UUID
    status: str
    onboarding: bool
    extract_client_id: uuid.UUID | None
    extract_attempts: int
    output_hash: str | None


@dataclass(frozen=True, slots=True)
class WindowTurn:
    id: uuid.UUID
    role: str
    content: str


async def fetch_window_for_update(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
) -> ExtractWindowRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, status, onboarding, extract_client_id, extract_attempts, output_hash "
                "FROM public.conversation_windows "
                "WHERE spirit_id = :spirit_id AND id = :window_id "
                "FOR UPDATE"
            ),
            {"spirit_id": spirit_id, "window_id": window_id},
        )
    ).first()
    if row is None:
        return None
    return ExtractWindowRow(
        id=row.id,
        status=str(row.status),
        onboarding=bool(row.onboarding),
        extract_client_id=row.extract_client_id,
        extract_attempts=int(row.extract_attempts),
        output_hash=row.output_hash,
    )


async def fetch_window_turns(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
) -> list[WindowTurn]:
    rows = (
        await session.execute(
            text(
                "SELECT id, role, content FROM public.messages "
                "WHERE spirit_id = :spirit_id AND conversation_window_id = :window_id "
                "AND role IN ('user', 'spirit') "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"spirit_id": spirit_id, "window_id": window_id},
        )
    ).all()
    return [WindowTurn(id=row.id, role=str(row.role), content=str(row.content)) for row in rows]


def turns_for_provider(rows: list[WindowTurn]) -> list[ExtractTurn]:
    turns: list[ExtractTurn] = []
    for row in rows:
        if row.role not in {"user", "spirit"}:
            continue
        turns.append(ExtractTurn.model_validate({"role": row.role, "content": row.content}))
    return turns


async def extract_client_id_in_use(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    client_id: uuid.UUID,
) -> bool:
    found = await session.scalar(
        text(
            "SELECT 1 FROM public.conversation_windows "
            "WHERE spirit_id = :spirit_id AND extract_client_id = :client_id "
            "AND id <> :window_id LIMIT 1"
        ),
        {"spirit_id": spirit_id, "client_id": client_id, "window_id": window_id},
    )
    return found is not None


async def claim_extracting(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    client_id: uuid.UUID,
) -> bool:
    row = (
        await session.execute(
            text(
                "UPDATE public.conversation_windows "
                "SET status = 'extracting', extract_client_id = :client_id, "
                "extract_attempts = extract_attempts + 1, last_error_code = NULL "
                "WHERE id = :window_id AND spirit_id = :spirit_id "
                "AND onboarding = false AND status IN ('ready', 'failed') "
                "AND (extract_client_id IS NULL OR extract_client_id = :client_id) "
                "RETURNING id"
            ),
            {"spirit_id": spirit_id, "window_id": window_id, "client_id": client_id},
        )
    ).first()
    return row is not None


async def mark_extracted(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    output_hash: str,
    now: datetime,
) -> None:
    await session.execute(
        text(
            "UPDATE public.conversation_windows "
            "SET status = 'extracted', extracted_at = :now, output_hash = :output_hash, "
            "last_error_code = NULL "
            "WHERE id = :window_id AND spirit_id = :spirit_id AND status = 'extracting'"
        ),
        {
            "spirit_id": spirit_id,
            "window_id": window_id,
            "output_hash": output_hash,
            "now": now,
        },
    )


async def insert_memory(
    session: AsyncSession,
    *,
    memory_id: uuid.UUID,
    spirit_id: uuid.UUID,
    memory_type: str,
    summary: str,
    tags: list[str],
    salience: int,
    confidence: float,
    source_message_id: uuid.UUID | None,
) -> MemoryRecord:
    row = (
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, tags, salience, confidence, "
                "status, source_message_id"
                ") VALUES ("
                ":id, :spirit_id, :type, :summary, :tags, :salience, :confidence, "
                "'active', :source_message_id"
                ") RETURNING id, type, summary, tags, salience, confidence, status, "
                "version, created_at"
            ).bindparams(bindparam("tags", type_=ARRAY(Text()))),
            {
                "id": memory_id,
                "spirit_id": spirit_id,
                "type": memory_type,
                "summary": summary,
                "tags": tags,
                "salience": salience,
                "confidence": confidence,
                "source_message_id": source_message_id,
            },
        )
    ).one()
    return _memory_record(row)


async def insert_style_sample(
    session: AsyncSession,
    *,
    sample_id: uuid.UUID,
    spirit_id: uuid.UUID,
    kind: str,
    text_value: str,
    window_id: uuid.UUID,
) -> StyleSampleRecord:
    inserted = (
        await session.execute(
            text(
                "INSERT INTO public.style_samples ("
                "id, spirit_id, kind, text, source_window_id"
                ") VALUES ("
                ":id, :spirit_id, :kind, :text, :window_id"
                ") ON CONFLICT (spirit_id, kind, text) DO NOTHING "
                "RETURNING id, kind, text, weight, status"
            ),
            {
                "id": sample_id,
                "spirit_id": spirit_id,
                "kind": kind,
                "text": text_value,
                "window_id": window_id,
            },
        )
    ).first()
    if inserted is not None:
        return _style_record(inserted)
    existing = (
        await session.execute(
            text(
                "SELECT id, kind, text, weight, status FROM public.style_samples "
                "WHERE spirit_id = :spirit_id AND kind = :kind AND text = :text"
            ),
            {"spirit_id": spirit_id, "kind": kind, "text": text_value},
        )
    ).one()
    return _style_record(existing)


async def fetch_window_memories(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
) -> list[MemoryRecord]:
    rows = (
        await session.execute(
            text(
                "SELECT m.id, m.type, m.summary, m.tags, m.salience, m.confidence, "
                "m.status, m.version, m.created_at "
                "FROM public.memories m "
                "WHERE m.spirit_id = :spirit_id AND m.source_message_id IN ("
                "SELECT id FROM public.messages "
                "WHERE spirit_id = :spirit_id AND conversation_window_id = :window_id "
                "AND role = 'user'"
                ") ORDER BY m.created_at ASC, m.id ASC"
            ),
            {"spirit_id": spirit_id, "window_id": window_id},
        )
    ).all()
    return [_memory_record(row) for row in rows]


async def fetch_window_style_samples(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
) -> list[StyleSampleRecord]:
    rows = (
        await session.execute(
            text(
                "SELECT id, kind, text, weight, status FROM public.style_samples "
                "WHERE spirit_id = :spirit_id AND source_window_id = :window_id "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"spirit_id": spirit_id, "window_id": window_id},
        )
    ).all()
    return [_style_record(row) for row in rows]


async def apply_trait_patch(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    deltas: dict[TraitDimension, int],
    bump_version: bool,
) -> None:
    assignments: list[str] = []
    params: dict[str, Any] = {"id": spirit_id, "user_id": owner_id}
    for dimension in TRAIT_COLUMNS:
        delta = int(deltas.get(dimension, 0))
        if delta == 0:
            continue
        assignments.append(
            f"{dimension} = GREATEST(0, LEAST(100, {dimension} + :{dimension}_delta))"
        )
        params[f"{dimension}_delta"] = delta
    if not assignments and not bump_version:
        return
    if bump_version:
        assignments.append("version = version + 1")
    await session.execute(
        text(
            "UPDATE public.spirits SET "
            + ", ".join(assignments)
            + " WHERE id = :id AND user_id = :user_id"
        ),
        params,
    )


def _memory_record(row: Any) -> MemoryRecord:
    tags = row.tags
    return MemoryRecord(
        id=row.id,
        type=str(row.type),
        summary=str(row.summary),
        tags=tuple(str(item) for item in tags) if tags else (),
        salience=int(row.salience),
        confidence=float(row.confidence),
        status=str(row.status),
        version=int(row.version),
        created_at=row.created_at,
    )


def _style_record(row: Any) -> StyleSampleRecord:
    return StyleSampleRecord(
        id=row.id,
        kind=str(row.kind),
        text=str(row.text),
        weight=int(row.weight),
        status=str(row.status),
    )
