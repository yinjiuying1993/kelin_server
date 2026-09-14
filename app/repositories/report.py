"""Owner-filtered seven-day reports. Spec §§8.10, 14.7–14.8, 15.2."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text

from app.domain.report import (
    REPORT_LINE_OPERATION,
    REPORT_PROMPT_VERSION,
    REPORT_RULES_VERSION,
    REPORT_TYPE,
    TOP_MEMORY_LIMIT,
)


@dataclass(frozen=True, slots=True)
class SpiritLock:
    id: uuid.UUID
    version: int
    name: str
    stage: str
    status: str
    closeness: int
    curiosity: int
    sharpness: int
    nocturnal: int
    stubborn: int
    scholar_marks: tuple[str, ...]
    invite_code: str
    hatched_at: datetime | None
    ordinary_dialogue_rounds: int


@dataclass(frozen=True, slots=True)
class ReportRow:
    id: uuid.UUID
    spirit_id: uuid.UUID
    status: str
    eligibility_snapshot: dict[str, Any]
    title: str | None
    spirit_snapshot: dict[str, Any]
    room_weather: str | None
    top_traits: list[Any]
    top_memory_ids: tuple[uuid.UUID, ...]
    top_memories_snapshot: list[Any]
    scholar_marks: tuple[str, ...]
    signature_line: str | None
    invite_code_snapshot: str | None
    rules_version: str
    prompt_version: str
    line_attempts: int
    generated_at: datetime | None
    version: int


@dataclass(frozen=True, slots=True)
class MemoryLiveRow:
    id: uuid.UUID
    type: str
    summary: str
    status: str


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    inserted: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _marks(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _uuids(value: object) -> tuple[uuid.UUID, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(uuid.UUID(str(item)) for item in value)
    return ()


def _object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _array(value: object) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    return []


def _report_row(row: Any) -> ReportRow:
    return ReportRow(
        id=row.id,
        spirit_id=row.spirit_id,
        status=str(row.status),
        eligibility_snapshot=_object(row.eligibility_snapshot),
        title=str(row.title) if row.title is not None else None,
        spirit_snapshot=_object(row.spirit_snapshot),
        room_weather=str(row.room_weather) if row.room_weather is not None else None,
        top_traits=_array(row.top_traits),
        top_memory_ids=_uuids(row.top_memory_ids),
        top_memories_snapshot=_array(row.top_memories_snapshot),
        scholar_marks=_marks(row.scholar_marks),
        signature_line=str(row.signature_line) if row.signature_line is not None else None,
        invite_code_snapshot=(
            str(row.invite_code_snapshot) if row.invite_code_snapshot is not None else None
        ),
        rules_version=str(row.rules_version),
        prompt_version=str(row.prompt_version),
        line_attempts=int(row.line_attempts),
        generated_at=_aware(row.generated_at),
        version=int(row.version),
    )


_REPORT_COLUMNS = (
    "id",
    "spirit_id",
    "status",
    "eligibility_snapshot",
    "title",
    "spirit_snapshot",
    "room_weather",
    "top_traits",
    "top_memory_ids",
    "top_memories_snapshot",
    "scholar_marks",
    "signature_line",
    "invite_code_snapshot",
    "rules_version",
    "prompt_version",
    "line_attempts",
    "generated_at",
    "version",
)
_REPORT_RETURNING = ", ".join(_REPORT_COLUMNS)
_REPORT_RETURNING_R = ", ".join(f"r.{name}" for name in _REPORT_COLUMNS)


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("reports cannot write in a read-only transaction")


async def lock_owned_spirit(session: AsyncSession, owner_id: uuid.UUID) -> SpiritLock | None:
    row = (
        await session.execute(
            text(
                "SELECT s.id, s.version, s.name, s.stage, s.status, "
                "s.closeness, s.curiosity, s.sharpness, s.nocturnal, s.stubborn, "
                "s.scholar_marks, s.invite_code, s.hatched_at, s.ordinary_dialogue_rounds "
                "FROM public.spirits s "
                "WHERE s.user_id = :user_id FOR UPDATE"
            ),
            {"user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return SpiritLock(
        id=row.id,
        version=int(row.version),
        name=str(row.name),
        stage=str(row.stage),
        status=str(row.status),
        closeness=int(row.closeness),
        curiosity=int(row.curiosity),
        sharpness=int(row.sharpness),
        nocturnal=int(row.nocturnal),
        stubborn=int(row.stubborn),
        scholar_marks=_marks(row.scholar_marks),
        invite_code=str(row.invite_code),
        hatched_at=_aware(row.hatched_at),
        ordinary_dialogue_rounds=int(row.ordinary_dialogue_rounds),
    )


async def bump_spirit_version(
    session: AsyncSession, *, owner_id: uuid.UUID, spirit_id: uuid.UUID
) -> int:
    value = await session.scalar(
        text(
            "UPDATE public.spirits SET version = version + 1 "
            "WHERE id = :id AND user_id = :user_id RETURNING version"
        ),
        {"id": spirit_id, "user_id": owner_id},
    )
    if value is None:
        raise RuntimeError("spirit version bump did not return a row")
    return int(value)


async def fetch_owned_report(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ReportRow | None:
    suffix = " FOR UPDATE" if for_update else ""
    row = (
        await session.execute(
            text(
                f"SELECT {_REPORT_RETURNING_R} "
                "FROM public.reports r "
                "JOIN public.spirits s ON s.id = r.spirit_id AND s.user_id = :user_id "
                "WHERE r.report_type = :report_type"
                f"{suffix}"
            ),
            {"user_id": owner_id, "report_type": REPORT_TYPE},
        )
    ).first()
    if row is None:
        return None
    return _report_row(row)


async def fetch_owned_report_by_id(
    session: AsyncSession,
    owner_id: uuid.UUID,
    report_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ReportRow | None:
    suffix = " FOR UPDATE" if for_update else ""
    row = (
        await session.execute(
            text(
                f"SELECT {_REPORT_RETURNING_R} "
                "FROM public.reports r "
                "JOIN public.spirits s ON s.id = r.spirit_id AND s.user_id = :user_id "
                "WHERE r.id = :report_id AND r.report_type = :report_type"
                f"{suffix}"
            ),
            {
                "user_id": owner_id,
                "report_id": report_id,
                "report_type": REPORT_TYPE,
            },
        )
    ).first()
    if row is None:
        return None
    return _report_row(row)


async def insert_generating_report(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    eligibility_snapshot: dict[str, Any],
) -> uuid.UUID | None:
    row = (
        await session.execute(
            text(
                "INSERT INTO public.reports ("
                "spirit_id, report_type, status, eligibility_snapshot, "
                "rules_version, prompt_version"
                ") VALUES ("
                ":spirit_id, :report_type, 'generating', CAST(:eligibility AS jsonb), "
                ":rules_version, :prompt_version"
                ") ON CONFLICT (spirit_id, report_type) DO NOTHING "
                "RETURNING id"
            ),
            {
                "spirit_id": spirit_id,
                "report_type": REPORT_TYPE,
                "eligibility": json.dumps(eligibility_snapshot),
                "rules_version": REPORT_RULES_VERSION,
                "prompt_version": REPORT_PROMPT_VERSION,
            },
        )
    ).first()
    if row is None:
        return None
    return row.id


async def insert_generate_outbox(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    report_id: uuid.UUID,
    spirit_id: uuid.UUID,
) -> bool:
    del owner_id
    inserted = await session.scalar(
        text("SELECT private.enqueue_report_generate(:report_id, :spirit_id)"),
        {"report_id": report_id, "spirit_id": spirit_id},
    )
    return bool(inserted)


async def select_top_active_memories(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    limit: int = TOP_MEMORY_LIMIT,
) -> tuple[MemoryLiveRow, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT m.id, m.type, m.summary, m.status "
                "FROM public.memories m "
                "JOIN public.spirits s ON s.id = m.spirit_id AND s.user_id = :user_id "
                "WHERE m.spirit_id = :spirit_id AND m.status = 'active' "
                "ORDER BY m.salience DESC, m.created_at DESC, m.id DESC "
                "LIMIT :limit"
            ),
            {"user_id": owner_id, "spirit_id": spirit_id, "limit": limit},
        )
    ).all()
    return tuple(
        MemoryLiveRow(id=row.id, type=str(row.type), summary=str(row.summary), status=str(row.status))
        for row in rows
    )


async def load_memories_for_ids(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    memory_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, MemoryLiveRow]:
    if not memory_ids:
        return {}
    statement = text(
        "SELECT m.id, m.type, m.summary, m.status "
        "FROM public.memories m "
        "JOIN public.spirits s ON s.id = m.spirit_id AND s.user_id = :user_id "
        "WHERE m.id = ANY(:ids)"
    ).bindparams(bindparam("ids", type_=ARRAY(PG_UUID(as_uuid=True))))
    rows = (
        await session.execute(
            statement,
            {"user_id": owner_id, "ids": list(memory_ids)},
        )
    ).all()
    return {
        row.id: MemoryLiveRow(
            id=row.id,
            type=str(row.type),
            summary=str(row.summary),
            status=str(row.status),
        )
        for row in rows
    }


async def latest_emotion(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
) -> str | None:
    value = await session.scalar(
        text(
            "SELECT f.payload->>'emotion' AS emotion "
            "FROM public.feeds f "
            "JOIN public.spirits s ON s.id = f.spirit_id AND s.user_id = :user_id "
            "WHERE f.kind = 'emotion' "
            "AND f.status = 'accepted' "
            "AND f.effect_applied_at IS NOT NULL "
            "ORDER BY f.effect_applied_at DESC, f.created_at DESC, f.id DESC "
            "LIMIT 1"
        ),
        {"user_id": owner_id},
    )
    if value is None:
        return None
    return str(value)


async def save_generated_card(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    report_id: uuid.UUID,
    expected_version: int,
    status: str,
    title: str,
    spirit_snapshot: dict[str, Any],
    room_weather: str,
    top_traits: list[dict[str, Any]],
    top_memory_ids: tuple[uuid.UUID, ...],
    top_memories_snapshot: list[dict[str, Any]],
    scholar_marks: tuple[str, ...],
    signature_line: str | None,
    invite_code: str,
    generated_at: datetime,
) -> ReportRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.reports r SET "
                "status = :status, title = :title, "
                "spirit_snapshot = CAST(:spirit_snapshot AS jsonb), "
                "room_weather = :room_weather, "
                "top_traits = CAST(:top_traits AS jsonb), "
                "top_memory_ids = :top_memory_ids, "
                "top_memories_snapshot = CAST(:top_memories_snapshot AS jsonb), "
                "scholar_marks = :scholar_marks, "
                "signature_line = :signature_line, "
                "invite_code_snapshot = :invite_code, "
                "generated_at = :generated_at, "
                "version = r.version + 1 "
                "FROM public.spirits s "
                "WHERE r.id = :report_id AND r.spirit_id = s.id AND s.user_id = :user_id "
                "AND r.version = :expected_version AND r.status = 'generating' "
                f"RETURNING {_REPORT_RETURNING_R}"
            ).bindparams(
                bindparam("top_memory_ids", type_=ARRAY(PG_UUID(as_uuid=True))),
                bindparam("scholar_marks", type_=ARRAY(Text())),
            ),
            {
                "status": status,
                "title": title,
                "spirit_snapshot": json.dumps(spirit_snapshot),
                "room_weather": room_weather,
                "top_traits": json.dumps(top_traits),
                "top_memory_ids": list(top_memory_ids),
                "top_memories_snapshot": json.dumps(top_memories_snapshot),
                "scholar_marks": list(scholar_marks),
                "signature_line": signature_line,
                "invite_code": invite_code,
                "generated_at": generated_at,
                "report_id": report_id,
                "user_id": owner_id,
                "expected_version": expected_version,
            },
        )
    ).first()
    if row is None:
        return None
    return _report_row(row)


async def mark_failed(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    report_id: uuid.UUID,
    expected_version: int,
) -> ReportRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.reports r SET status = 'failed', version = r.version + 1 "
                "FROM public.spirits s "
                "WHERE r.id = :report_id AND r.spirit_id = s.id AND s.user_id = :user_id "
                "AND r.version = :expected_version AND r.status = 'generating' "
                f"RETURNING {_REPORT_RETURNING_R}"
            ),
            {
                "report_id": report_id,
                "user_id": owner_id,
                "expected_version": expected_version,
            },
        )
    ).first()
    if row is None:
        return None
    return _report_row(row)


async def reserve_line_attempt(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    report_id: uuid.UUID,
    expected_version: int,
) -> ReportRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.reports r SET "
                "line_attempts = r.line_attempts + 1, version = r.version + 1 "
                "FROM public.spirits s "
                "WHERE r.id = :report_id AND r.spirit_id = s.id AND s.user_id = :user_id "
                "AND r.version = :expected_version AND r.status = 'partial' "
                "AND r.signature_line IS NULL AND r.line_attempts < 3 "
                f"RETURNING {_REPORT_RETURNING_R}"
            ),
            {
                "report_id": report_id,
                "user_id": owner_id,
                "expected_version": expected_version,
            },
        )
    ).first()
    if row is None:
        return None
    return _report_row(row)


async def save_signature_line(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    report_id: uuid.UUID,
    expected_version: int,
    signature_line: str,
) -> ReportRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.reports r SET "
                "signature_line = :signature_line, status = 'ready', "
                "version = r.version + 1 "
                "FROM public.spirits s "
                "WHERE r.id = :report_id AND r.spirit_id = s.id AND s.user_id = :user_id "
                "AND r.version = :expected_version AND r.status = 'partial' "
                "AND r.signature_line IS NULL "
                f"RETURNING {_REPORT_RETURNING_R}"
            ),
            {
                "signature_line": signature_line,
                "report_id": report_id,
                "user_id": owner_id,
                "expected_version": expected_version,
            },
        )
    ).first()
    if row is None:
        return None
    return _report_row(row)


async def claim_line_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> IdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": REPORT_LINE_OPERATION,
        "client_id": client_id,
        "request_hash": request_hash,
    }
    for _ in range(3):
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO public.idempotency_records ("
                    "user_id, operation, client_id, request_hash, status"
                    ") VALUES ("
                    ":user_id, :operation, :client_id, :request_hash, 'in_progress'"
                    ") ON CONFLICT (user_id, operation, client_id) DO NOTHING "
                    "RETURNING request_hash, status, resource_id"
                ),
                params,
            )
        ).first()
        if inserted is not None:
            return IdempotencyClaim(
                inserted=True,
                request_hash=str(inserted.request_hash),
                status=str(inserted.status),
                resource_id=inserted.resource_id,
            )
        existing = (
            await session.execute(
                text(
                    "SELECT request_hash, status, resource_id "
                    "FROM public.idempotency_records "
                    "WHERE user_id = :user_id AND operation = :operation "
                    "AND client_id = :client_id "
                    "FOR UPDATE"
                ),
                params,
            )
        ).first()
        if existing is not None:
            return IdempotencyClaim(
                inserted=False,
                request_hash=str(existing.request_hash),
                status=str(existing.status),
                resource_id=existing.resource_id,
            )
    raise RuntimeError("report line idempotency claim failed")


async def complete_line_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    resource_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = 'report', "
            "resource_id = :resource_id, completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "resource_id": resource_id,
            "user_id": owner_id,
            "operation": REPORT_LINE_OPERATION,
            "client_id": client_id,
        },
    )
