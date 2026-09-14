"""Owner-filtered pact persistence. Spec §§7.3, 8.8, 13.1–13.2."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.pact import (
    PACT_ACTIVE_UNIQUE,
    PACT_CREATE_OPERATION,
    PACT_SESSION_CLIENT_UNIQUE,
    PACT_SESSION_DATE_UNIQUE,
)

_PACT_RETURNING = (
    "id, spirit_id, client_id, theme, title, notes_memory_id, question_bank_version, "
    "week_start, starts_at, ends_at, status, completed_sessions, skipped_sessions, "
    "completeness, scholar_mark, version, created_at"
)
_SESSION_RETURNING = (
    "id, pact_id, session_date, day_index, client_id, status, explain, questions, "
    "answers, score, feedback, version"
)


@dataclass(frozen=True, slots=True)
class LockedPactSpirit:
    id: uuid.UUID
    version: int
    timezone: str


@dataclass(frozen=True, slots=True)
class PactRow:
    id: uuid.UUID
    spirit_id: uuid.UUID
    client_id: uuid.UUID
    theme: str
    title: str
    notes_memory_id: uuid.UUID | None
    question_bank_version: str
    week_start: date
    starts_at: datetime
    ends_at: datetime
    status: str
    completed_sessions: int
    skipped_sessions: int
    completeness: int
    scholar_mark: str | None
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    inserted: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class NotesMemoryRow:
    id: uuid.UUID
    type: str
    status: str
    summary: str


@dataclass(frozen=True, slots=True)
class PactSessionRow:
    id: uuid.UUID
    pact_id: uuid.UUID
    session_date: date
    day_index: int
    client_id: uuid.UUID
    status: str
    explain: str
    questions: Any
    answers: Any
    score: int | None
    feedback: Any
    version: int


@dataclass(frozen=True, slots=True)
class PactMistakeRow:
    question_id: str
    category: str
    summary: str
    times_seen: int


def integrity_constraint_name(exc: BaseException) -> str | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = _attr_str(current, "constraint_name")
        diag = getattr(current, "diag", None)
        if diag is not None:
            name = _attr_str(diag, "constraint_name") or name
        if name is not None:
            return name
        nxt = current.__cause__ or current.__context__
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException) and orig is not current:
            current = orig
        elif isinstance(nxt, BaseException) and nxt is not current:
            current = nxt
        else:
            current = None
    blob = str(exc).lower()
    if PACT_ACTIVE_UNIQUE in blob:
        return PACT_ACTIVE_UNIQUE
    if PACT_SESSION_DATE_UNIQUE in blob:
        return PACT_SESSION_DATE_UNIQUE
    if PACT_SESSION_CLIENT_UNIQUE in blob:
        return PACT_SESSION_CLIENT_UNIQUE
    return None


def _attr_str(obj: object, name: str) -> str | None:
    value = getattr(obj, name, None)
    if value is None:
        return None
    text_value = str(value)
    return text_value if text_value else None


def is_active_pact_conflict(exc: BaseException) -> bool:
    return integrity_constraint_name(exc) == PACT_ACTIVE_UNIQUE


def is_session_date_conflict(exc: BaseException) -> bool:
    return integrity_constraint_name(exc) == PACT_SESSION_DATE_UNIQUE


def is_session_client_conflict(exc: BaseException) -> bool:
    return integrity_constraint_name(exc) == PACT_SESSION_CLIENT_UNIQUE


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("pact cannot write in a read-only transaction")


async def lock_owned_spirit(session: AsyncSession, owner_id: uuid.UUID) -> LockedPactSpirit | None:
    row = (
        await session.execute(
            text(
                "SELECT s.id, s.version, COALESCE(p.timezone, 'Asia/Shanghai') AS timezone "
                "FROM public.spirits s "
                "LEFT JOIN public.user_preferences p ON p.user_id = s.user_id "
                "WHERE s.user_id = :user_id FOR UPDATE OF s"
            ),
            {"user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return LockedPactSpirit(id=row.id, version=int(row.version), timezone=str(row.timezone))


async def fetch_owned_notes_memory(
    session: AsyncSession, *, owner_id: uuid.UUID, memory_id: uuid.UUID
) -> NotesMemoryRow | None:
    row = (
        await session.execute(
            text(
                "SELECT m.id, m.type, m.status, m.summary "
                "FROM public.memories m "
                "JOIN public.spirits s ON s.id = m.spirit_id "
                "WHERE m.id = :id AND s.user_id = :user_id"
            ),
            {"id": memory_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return NotesMemoryRow(
        id=row.id,
        type=str(row.type),
        status=str(row.status),
        summary=str(row.summary),
    )


async def fetch_active_pact(
    session: AsyncSession, *, owner_id: uuid.UUID, spirit_id: uuid.UUID
) -> PactRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_PACT_RETURNING} FROM public.pacts "
                "WHERE spirit_id = :spirit_id AND status = 'active' "
                "AND spirit_id IN (SELECT id FROM public.spirits WHERE user_id = :user_id)"
            ),
            {"spirit_id": spirit_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _pact_row(row)


async def fetch_pact_by_id(
    session: AsyncSession, *, owner_id: uuid.UUID, pact_id: uuid.UUID
) -> PactRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_PACT_RETURNING} FROM public.pacts "
                "WHERE id = :id "
                "AND spirit_id IN (SELECT id FROM public.spirits WHERE user_id = :user_id)"
            ),
            {"id": pact_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _pact_row(row)


async def fetch_pact_by_client_id(
    session: AsyncSession, *, owner_id: uuid.UUID, client_id: uuid.UUID
) -> PactRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_PACT_RETURNING} FROM public.pacts "
                "WHERE client_id = :client_id "
                "AND spirit_id IN (SELECT id FROM public.spirits WHERE user_id = :user_id)"
            ),
            {"client_id": client_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _pact_row(row)


async def insert_pact(
    session: AsyncSession,
    *,
    pact_id: uuid.UUID,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    client_id: uuid.UUID,
    theme: str,
    title: str,
    notes_memory_id: uuid.UUID | None,
    question_bank_version: str,
    week_start: date,
    starts_at: datetime,
    ends_at: datetime,
) -> PactRow:
    row = (
        await session.execute(
            text(
                "INSERT INTO public.pacts ("
                "id, spirit_id, client_id, theme, title, notes_memory_id, "
                "question_bank_version, week_start, starts_at, ends_at, status"
                ") SELECT :id, s.id, :client_id, :theme, :title, :notes_memory_id, "
                ":question_bank_version, :week_start, :starts_at, :ends_at, 'active' "
                "FROM public.spirits s "
                "WHERE s.id = :spirit_id AND s.user_id = :user_id "
                f"RETURNING {_PACT_RETURNING}"
            ),
            {
                "id": pact_id,
                "user_id": owner_id,
                "spirit_id": spirit_id,
                "client_id": client_id,
                "theme": theme,
                "title": title,
                "notes_memory_id": notes_memory_id,
                "question_bank_version": question_bank_version,
                "week_start": week_start,
                "starts_at": starts_at,
                "ends_at": ends_at,
            },
        )
    ).first()
    if row is None:
        raise RuntimeError("pact insert did not return a row")
    return _pact_row(row)


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


async def lock_owned_pact(
    session: AsyncSession, *, owner_id: uuid.UUID, pact_id: uuid.UUID
) -> PactRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_PACT_RETURNING} FROM public.pacts p "
                "WHERE p.id = :id "
                "AND p.spirit_id IN (SELECT id FROM public.spirits WHERE user_id = :user_id) "
                "FOR UPDATE OF p"
            ),
            {"id": pact_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _pact_row(row)


async def fetch_session_by_id(
    session: AsyncSession, *, owner_id: uuid.UUID, session_id: uuid.UUID
) -> PactSessionRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_SESSION_RETURNING} FROM public.pact_sessions sess "
                "WHERE sess.id = :id "
                "AND sess.pact_id IN ("
                "SELECT p.id FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE s.user_id = :user_id"
                ")"
            ),
            {"id": session_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _session_row(row)


async def fetch_session_by_date(
    session: AsyncSession, *, owner_id: uuid.UUID, pact_id: uuid.UUID, session_date: date
) -> PactSessionRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_SESSION_RETURNING} FROM public.pact_sessions sess "
                "WHERE sess.pact_id = :pact_id AND sess.session_date = :session_date "
                "AND sess.pact_id IN ("
                "SELECT p.id FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE s.user_id = :user_id"
                ")"
            ),
            {"pact_id": pact_id, "session_date": session_date, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _session_row(row)


async def lock_owned_session_by_date(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    pact_id: uuid.UUID,
    session_date: date,
) -> PactSessionRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_SESSION_RETURNING} FROM public.pact_sessions sess "
                "WHERE sess.pact_id = :pact_id AND sess.session_date = :session_date "
                "AND sess.pact_id IN ("
                "SELECT p.id FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE s.user_id = :user_id"
                ") FOR UPDATE OF sess"
            ),
            {"pact_id": pact_id, "session_date": session_date, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _session_row(row)


async def fetch_session_by_client_id(
    session: AsyncSession, *, owner_id: uuid.UUID, pact_id: uuid.UUID, client_id: uuid.UUID
) -> PactSessionRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_SESSION_RETURNING} FROM public.pact_sessions sess "
                "WHERE sess.pact_id = :pact_id AND sess.client_id = :client_id "
                "AND sess.pact_id IN ("
                "SELECT p.id FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE s.user_id = :user_id"
                ")"
            ),
            {"pact_id": pact_id, "client_id": client_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _session_row(row)


async def lock_owned_session(
    session: AsyncSession, *, owner_id: uuid.UUID, session_id: uuid.UUID
) -> PactSessionRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_SESSION_RETURNING} FROM public.pact_sessions sess "
                "WHERE sess.id = :id "
                "AND sess.pact_id IN ("
                "SELECT p.id FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE s.user_id = :user_id"
                ") FOR UPDATE OF sess"
            ),
            {"id": session_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _session_row(row)


async def save_session_answers(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    expected_version: int,
    answers_json: str,
) -> PactSessionRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.pact_sessions sess "
                "SET answers = CAST(:answers AS jsonb), version = version + 1, "
                "updated_at = now() "
                "WHERE sess.id = :id AND sess.version = :expected "
                "AND sess.pact_id IN ("
                "SELECT p.id FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE s.user_id = :user_id"
                f") RETURNING {_SESSION_RETURNING}"
            ),
            {
                "id": session_id,
                "user_id": owner_id,
                "expected": expected_version,
                "answers": answers_json,
            },
        )
    ).first()
    if row is None:
        return None
    return _session_row(row)


async def save_last_answer_and_finalize(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    expected_version: int,
    answers_json: str,
    score: int,
    feedback_json: str,
    now: datetime,
) -> PactSessionRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.pact_sessions sess "
                "SET answers = CAST(:answers AS jsonb), status = 'answered', "
                "score = :score, feedback = CAST(:feedback AS jsonb), "
                "submitted_at = :now, version = version + 1, updated_at = now() "
                "WHERE sess.id = :id AND sess.version = :expected AND sess.status = 'ready' "
                "AND sess.pact_id IN ("
                "SELECT p.id FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE s.user_id = :user_id"
                f") RETURNING {_SESSION_RETURNING}"
            ),
            {
                "id": session_id,
                "user_id": owner_id,
                "expected": expected_version,
                "answers": answers_json,
                "score": score,
                "feedback": feedback_json,
                "now": now,
            },
        )
    ).first()
    if row is None:
        return None
    return _session_row(row)


async def apply_completed_session(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    pact_id: uuid.UUID,
    completeness: int,
) -> PactRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.pacts p "
                "SET completed_sessions = completed_sessions + 1, "
                "completeness = :completeness, version = version + 1, updated_at = now() "
                "WHERE p.id = :id AND p.status = 'active' "
                "AND p.spirit_id IN (SELECT id FROM public.spirits WHERE user_id = :user_id) "
                f"RETURNING {_PACT_RETURNING}"
            ),
            {"id": pact_id, "user_id": owner_id, "completeness": completeness},
        )
    ).first()
    if row is None:
        return None
    return _pact_row(row)


async def apply_skipped_session(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    pact_id: uuid.UUID,
    completeness: int,
) -> PactRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.pacts p "
                "SET skipped_sessions = skipped_sessions + 1, "
                "completeness = :completeness, version = version + 1, updated_at = now() "
                "WHERE p.id = :id AND p.status = 'active' "
                "AND p.spirit_id IN (SELECT id FROM public.spirits WHERE user_id = :user_id) "
                f"RETURNING {_PACT_RETURNING}"
            ),
            {"id": pact_id, "user_id": owner_id, "completeness": completeness},
        )
    ).first()
    if row is None:
        return None
    return _pact_row(row)


async def skip_ready_session(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    session_id: uuid.UUID,
    now: datetime,
) -> PactSessionRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.pact_sessions sess "
                "SET status = 'skipped', skipped_at = :now, version = version + 1, "
                "updated_at = now() "
                "WHERE sess.id = :id AND sess.status = 'ready' "
                "AND sess.pact_id IN ("
                "SELECT p.id FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE s.user_id = :user_id"
                f") RETURNING {_SESSION_RETURNING}"
            ),
            {"id": session_id, "user_id": owner_id, "now": now},
        )
    ).first()
    if row is None:
        return None
    return _session_row(row)


async def insert_skipped_session(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    owner_id: uuid.UUID,
    pact_id: uuid.UUID,
    session_date: date,
    day_index: int,
    client_id: uuid.UUID,
    explain: str,
    questions_json: str,
    now: datetime,
) -> PactSessionRow:
    row = (
        await session.execute(
            text(
                "INSERT INTO public.pact_sessions ("
                "id, pact_id, session_date, day_index, client_id, status, explain, "
                "questions, skipped_at"
                ") SELECT :id, p.id, :session_date, :day_index, :client_id, 'skipped', "
                ":explain, CAST(:questions AS jsonb), :now "
                "FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE p.id = :pact_id AND s.user_id = :user_id "
                f"RETURNING {_SESSION_RETURNING}"
            ),
            {
                "id": session_id,
                "user_id": owner_id,
                "pact_id": pact_id,
                "session_date": session_date,
                "day_index": day_index,
                "client_id": client_id,
                "explain": explain,
                "questions": questions_json,
                "now": now,
            },
        )
    ).first()
    if row is None:
        raise RuntimeError("pact skip insert did not return a row")
    return _session_row(row)


async def complete_pact_if_eligible(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    pact_id: uuid.UUID,
    scholar_mark: str,
    now: datetime,
    min_sessions: int,
    min_completeness: int,
) -> PactRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.pacts p "
                "SET status = 'completed', scholar_mark = :scholar_mark, "
                "completed_at = :now, version = version + 1, updated_at = now() "
                "WHERE p.id = :id AND p.status = 'active' "
                "AND p.completed_sessions >= :min_sessions "
                "AND p.completeness >= :min_completeness "
                "AND p.spirit_id IN (SELECT id FROM public.spirits WHERE user_id = :user_id) "
                f"RETURNING {_PACT_RETURNING}"
            ),
            {
                "id": pact_id,
                "user_id": owner_id,
                "scholar_mark": scholar_mark,
                "now": now,
                "min_sessions": min_sessions,
                "min_completeness": min_completeness,
            },
        )
    ).first()
    if row is None:
        return None
    return _pact_row(row)


async def insert_session_mistake(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    pact_id: uuid.UUID,
    session_id: uuid.UUID,
    question_id: str,
    category: str,
    summary: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.pact_mistakes ("
            "pact_id, session_id, question_id, category, summary"
            ") SELECT p.id, :session_id, :question_id, :category, :summary "
            "FROM public.pacts p "
            "JOIN public.spirits s ON s.id = p.spirit_id "
            "WHERE p.id = :pact_id AND s.user_id = :user_id "
            "ON CONFLICT (session_id, question_id) DO NOTHING"
        ),
        {
            "user_id": owner_id,
            "pact_id": pact_id,
            "session_id": session_id,
            "question_id": question_id,
            "category": category,
            "summary": summary,
        },
    )


async def list_session_mistakes(
    session: AsyncSession, *, owner_id: uuid.UUID, session_id: uuid.UUID
) -> tuple[PactMistakeRow, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT m.question_id, m.category, m.summary, m.times_seen "
                "FROM public.pact_mistakes m "
                "JOIN public.pacts p ON p.id = m.pact_id "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE m.session_id = :session_id AND s.user_id = :user_id "
                "ORDER BY m.created_at, m.question_id"
            ),
            {"session_id": session_id, "user_id": owner_id},
        )
    ).all()
    return tuple(
        PactMistakeRow(
            question_id=str(row.question_id),
            category=str(row.category),
            summary=str(row.summary),
            times_seen=int(row.times_seen),
        )
        for row in rows
    )


async def insert_session(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    owner_id: uuid.UUID,
    pact_id: uuid.UUID,
    session_date: date,
    day_index: int,
    client_id: uuid.UUID,
    explain: str,
    questions_json: str,
) -> PactSessionRow:
    row = (
        await session.execute(
            text(
                "INSERT INTO public.pact_sessions ("
                "id, pact_id, session_date, day_index, client_id, status, explain, questions"
                ") SELECT :id, p.id, :session_date, :day_index, :client_id, 'ready', "
                ":explain, CAST(:questions AS jsonb) "
                "FROM public.pacts p "
                "JOIN public.spirits s ON s.id = p.spirit_id "
                "WHERE p.id = :pact_id AND s.user_id = :user_id "
                f"RETURNING {_SESSION_RETURNING}"
            ),
            {
                "id": session_id,
                "user_id": owner_id,
                "pact_id": pact_id,
                "session_date": session_date,
                "day_index": day_index,
                "client_id": client_id,
                "explain": explain,
                "questions": questions_json,
            },
        )
    ).first()
    if row is None:
        raise RuntimeError("pact session insert did not return a row")
    return _session_row(row)


async def claim_pact_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
    *,
    operation: str = PACT_CREATE_OPERATION,
) -> IdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": operation,
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
    raise RuntimeError("pact idempotency claim failed")


async def complete_pact_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    resource_id: uuid.UUID,
    *,
    operation: str = PACT_CREATE_OPERATION,
    resource_type: str = "pact",
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = :resource_type, "
            "resource_id = :resource_id, completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": operation,
            "client_id": client_id,
            "resource_id": resource_id,
            "resource_type": resource_type,
        },
    )


def _pact_row(row: Any) -> PactRow:
    notes = row.notes_memory_id
    return PactRow(
        id=row.id,
        spirit_id=row.spirit_id,
        client_id=row.client_id,
        theme=str(row.theme),
        title=str(row.title),
        notes_memory_id=notes if notes is None else uuid.UUID(str(notes)),
        question_bank_version=str(row.question_bank_version),
        week_start=row.week_start,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        status=str(row.status),
        completed_sessions=int(row.completed_sessions),
        skipped_sessions=int(row.skipped_sessions),
        completeness=int(row.completeness),
        scholar_mark=None if row.scholar_mark is None else str(row.scholar_mark),
        version=int(row.version),
        created_at=row.created_at,
    )


def _session_row(row: Any) -> PactSessionRow:
    return PactSessionRow(
        id=row.id,
        pact_id=row.pact_id,
        session_date=row.session_date,
        day_index=int(row.day_index),
        client_id=row.client_id,
        status=str(row.status),
        explain=str(row.explain),
        questions=row.questions,
        answers=row.answers,
        score=None if row.score is None else int(row.score),
        feedback=row.feedback,
        version=int(row.version),
    )
