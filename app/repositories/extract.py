"""Owner-filtered extract persistence. Spec §§8.3, 10.3."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.extract import MemoryRecord
from app.providers.types import ExtractTurn


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


async def mark_extract_failed(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    client_id: uuid.UUID,
    error_code: str,
) -> None:
    await session.execute(
        text(
            "UPDATE public.conversation_windows "
            "SET status = 'failed', last_error_code = :error_code "
            "WHERE id = :window_id AND spirit_id = :spirit_id "
            "AND status = 'extracting' AND extract_client_id = :client_id"
        ),
        {
            "spirit_id": spirit_id,
            "window_id": window_id,
            "client_id": client_id,
            "error_code": error_code,
        },
    )


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
