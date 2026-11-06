"""Apply an already computed memory decision inside the owner transaction."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.extract import ExtractSourceTurn, MemoryRecord, PriorUserMessage
from app.domain.memory.topic import masked_topics, remembered_topics
from app.domain.memory.types import MemoryDecision, MemoryIndexItem, MemoryStatus

_MEMORY_COLUMNS = (
    "id, type, summary, tags, salience, confidence, status, version, created_at, "
    "confirm_count, topic_key, source_event_id, decision_audit, last_confirmed_at, "
    "corrected_from_id"
)


async def load_memory_index(
    session: AsyncSession,
    spirit_id: uuid.UUID,
) -> tuple[MemoryIndexItem, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT id, type, topic_key, summary, status, salience, confidence, "
                "confirm_count, COALESCE(last_confirmed_at, created_at) AS confirmed_at, "
                "source_event_id "
                "FROM public.memories "
                "WHERE spirit_id = :spirit_id "
                "AND topic_key IS NOT NULL AND source_event_id IS NOT NULL"
            ),
            {"spirit_id": spirit_id},
        )
    ).all()
    items: list[MemoryIndexItem] = []
    for row in rows:
        status = str(row.status)
        if status not in {"active", "rejected", "sealed", "deleted", "superseded", "stale"}:
            continue
        items.append(
            MemoryIndexItem(
                memory_id=row.id,
                memory_type=str(row.type),
                topic_key=str(row.topic_key),
                summary=str(row.summary),
                status=_status(status),
                salience=int(row.salience),
                confidence=float(row.confidence),
                confirm_count=int(row.confirm_count),
                last_confirmed_at=row.confirmed_at,
                source_event_id=row.source_event_id,
            )
        )
    return tuple(items)


async def apply_memory_decision(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    decision: MemoryDecision,
    now: datetime,
    tags: list[str] | None = None,
    source_message_id: uuid.UUID | None = None,
    source_feed_id: uuid.UUID | None = None,
) -> MemoryRecord | None:
    """accept inserts, merge updates, supersede replaces, reject writes nothing."""
    existing = await _find_event(session, spirit_id, decision.source_event_id)
    if existing is not None:
        return _memory_record(existing)
    if decision.decision == "reject":
        return None
    if decision.decision == "merge":
        return await _merge(session, spirit_id=spirit_id, decision=decision, now=now)
    if decision.decision == "supersede":
        return await _supersede(
            session,
            spirit_id=spirit_id,
            decision=decision,
            now=now,
            tags=tags or [],
            source_message_id=source_message_id,
        )
    if decision.decision == "accept":
        return await _insert(
            session,
            spirit_id=spirit_id,
            decision=decision,
            now=now,
            tags=tags or [],
            source_message_id=source_message_id,
            source_feed_id=source_feed_id,
            corrected_from_id=None,
        )
    return None


async def _merge(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    decision: MemoryDecision,
    now: datetime,
) -> MemoryRecord | None:
    if decision.conflict_target_id is None:
        return None
    current = await _lock(session, spirit_id, decision.conflict_target_id)
    if current is None or str(current.status) != "active":
        return None
    if decision.confirm_delta <= 0:
        return _memory_record(current)
    audit = _audit_dict(current.decision_audit)
    stored_ids = audit.get("event_ids", [])
    event_ids = [str(item) for item in stored_ids] if isinstance(stored_ids, list) else []
    event_id = str(decision.source_event_id)
    if event_id not in event_ids:
        event_ids.append(event_id)
    audit.update(
        {
            "decision": "merge",
            "candidate_id": str(decision.candidate_id),
            "rules_version": decision.rules_version,
            "confirm_delta": decision.confirm_delta,
            "event_ids": event_ids,
        }
    )
    salience = int(current.salience)
    if decision.salience is not None:
        salience = max(salience, decision.salience)
    updated = await session.execute(
        _audit_sql(
            "UPDATE public.memories SET "
            "confirm_count = confirm_count + :delta, "
            "last_confirmed_at = :now, salience = :salience, "
            "decision_audit = :audit, updated_at = :now "
            "WHERE id = :memory_id AND spirit_id = :spirit_id AND status = 'active' "
            f"RETURNING {_MEMORY_COLUMNS}"
        ),
        {
            "delta": decision.confirm_delta,
            "now": now,
            "salience": salience,
            "audit": audit,
            "memory_id": current.id,
            "spirit_id": spirit_id,
        },
    )
    row = updated.one_or_none()
    if row is None:
        return None
    return _memory_record(row)


async def _supersede(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    decision: MemoryDecision,
    now: datetime,
    tags: list[str],
    source_message_id: uuid.UUID | None,
) -> MemoryRecord | None:
    if decision.conflict_target_id is None:
        return None
    current = await _lock(session, spirit_id, decision.conflict_target_id)
    if current is None or str(current.status) != "active":
        return None
    replaced = await session.execute(
        text(
            "UPDATE public.memories SET status = 'superseded', updated_at = :now "
            "WHERE id = :memory_id AND spirit_id = :spirit_id AND status = 'active' "
            "RETURNING id"
        ),
        {"now": now, "memory_id": current.id, "spirit_id": spirit_id},
    )
    if replaced.one_or_none() is None:
        return None
    return await _insert(
        session,
        spirit_id=spirit_id,
        decision=decision,
        now=now,
        tags=tags,
        source_message_id=source_message_id,
        source_feed_id=None,
        corrected_from_id=current.id,
    )


async def _insert(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    decision: MemoryDecision,
    now: datetime,
    tags: list[str],
    source_message_id: uuid.UUID | None,
    source_feed_id: uuid.UUID | None,
    corrected_from_id: uuid.UUID | None,
) -> MemoryRecord | None:
    if (
        decision.memory_type is None
        or decision.summary is None
        or decision.normalized_topic_key is None
        or decision.salience is None
        or decision.confidence is None
    ):
        return None
    message_id = source_message_id
    audit: dict[str, Any] = {
        "decision": decision.decision,
        "candidate_id": str(decision.candidate_id),
        "rules_version": decision.rules_version,
        "confirm_delta": decision.confirm_delta,
        "event_ids": [str(decision.source_event_id)],
    }
    if corrected_from_id is not None:
        audit["replaced_memory_id"] = str(corrected_from_id)
    inserted = await session.execute(
        text(
            "INSERT INTO public.memories ("
            "id, spirit_id, type, summary, tags, salience, confidence, status, "
            "source_message_id, source_feed_id, topic_key, source_event_id, rules_version, "
            "confirm_count, last_confirmed_at, decision_audit, corrected_from_id, "
            "updated_at"
            ") VALUES ("
            ":id, :spirit_id, :memory_type, :summary, :tags, :salience, :confidence, "
            "'active', :source_message_id, :source_feed_id, :topic_key, :source_event_id, "
            ":rules_version, 1, :now, :audit, :corrected_from_id, :now"
            ") ON CONFLICT (spirit_id, source_event_id) "
            "WHERE source_event_id IS NOT NULL DO NOTHING "
            f"RETURNING {_MEMORY_COLUMNS}"
        ).bindparams(
            bindparam("audit", type_=JSONB),
            bindparam("tags", type_=ARRAY(Text())),
        ),
        {
            "id": uuid.uuid4(),
            "spirit_id": spirit_id,
            "memory_type": decision.memory_type,
            "summary": decision.summary,
            "tags": tags,
            "salience": decision.salience,
            "confidence": decision.confidence,
            "source_message_id": message_id,
            "source_feed_id": source_feed_id,
            "topic_key": decision.normalized_topic_key,
            "source_event_id": decision.source_event_id,
            "rules_version": decision.rules_version,
            "now": now,
            "audit": audit,
            "corrected_from_id": corrected_from_id,
        },
    )
    row = inserted.one_or_none()
    if row is not None:
        return _memory_record(row)
    stored = await _find_event(session, spirit_id, decision.source_event_id)
    if stored is None:
        return None
    return _memory_record(stored)


def _audit_sql(statement: str):
    return text(statement).bindparams(bindparam("audit", type_=JSONB))


async def _find_event(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    source_event_id: uuid.UUID,
) -> Any | None:
    return (
        await session.execute(
            text(
                f"SELECT {_MEMORY_COLUMNS} FROM public.memories "
                "WHERE spirit_id = :spirit_id AND ("
                "source_event_id = :source_event_id OR "
                "jsonb_exists(COALESCE(decision_audit -> 'event_ids', '[]'::jsonb), :event_text)"
                ") LIMIT 1"
            ),
            {
                "spirit_id": spirit_id,
                "source_event_id": source_event_id,
                "event_text": str(source_event_id),
            },
        )
    ).one_or_none()


async def _lock(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    memory_id: uuid.UUID,
) -> Any | None:
    return (
        await session.execute(
            text(
                f"SELECT {_MEMORY_COLUMNS} FROM public.memories "
                "WHERE id = :memory_id AND spirit_id = :spirit_id FOR UPDATE"
            ),
            {"memory_id": memory_id, "spirit_id": spirit_id},
        )
    ).one_or_none()


def _status(value: str) -> MemoryStatus:
    if value == "active":
        return "active"
    if value == "rejected":
        return "rejected"
    if value == "sealed":
        return "sealed"
    if value == "deleted":
        return "deleted"
    if value == "superseded":
        return "superseded"
    return "stale"


async def revise_feed_memory(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    feed_id: uuid.UUID,
    event_id: uuid.UUID,
    promise_state: str,
    now: datetime,
) -> MemoryRecord | None:
    """Update one promise memory in place. Salience and confirm_count stay put."""
    current = (
        await session.execute(
            text(
                f"SELECT {_MEMORY_COLUMNS} FROM public.memories "
                "WHERE spirit_id = :spirit_id AND source_feed_id = :feed_id "
                "AND status = 'active' FOR UPDATE"
            ),
            {"spirit_id": spirit_id, "feed_id": feed_id},
        )
    ).one_or_none()
    if current is None:
        return None
    audit = _audit_dict(current.decision_audit)
    stored_ids = audit.get("event_ids", [])
    event_ids = [str(item) for item in stored_ids] if isinstance(stored_ids, list) else []
    token = str(event_id)
    if token in event_ids:
        return _memory_record(current)
    event_ids.append(token)
    audit.update({"decision": "merge", "event_ids": event_ids, "promise_state": promise_state})
    summary = _promise_summary(str(current.summary), promise_state)
    updated = await session.execute(
        _audit_sql(
            "UPDATE public.memories SET summary = :summary, decision_audit = :audit, "
            "updated_at = :now "
            "WHERE id = :memory_id AND spirit_id = :spirit_id AND status = 'active' "
            f"RETURNING {_MEMORY_COLUMNS}"
        ),
        {
            "summary": summary,
            "audit": audit,
            "now": now,
            "memory_id": current.id,
            "spirit_id": spirit_id,
        },
    )
    row = updated.one_or_none()
    if row is None:
        return None
    return _memory_record(row)


def _promise_summary(summary: str, promise_state: str) -> str:
    marker = f"状态 {promise_state}"
    if "状态 open" in summary:
        return summary.replace("状态 open", marker, 1)
    if marker in summary:
        return summary
    return f"{summary}，{marker}"


async def load_topic_masks(session: AsyncSession, spirit_id: uuid.UUID) -> frozenset[str]:
    rows = (
        await session.execute(
            text(
                "SELECT topic_key FROM public.memories "
                "WHERE spirit_id = :spirit_id AND status = 'superseded' "
                "AND topic_key IS NOT NULL "
                "AND decision_audit->>'rejection_code' = 'DO_NOT_STORE'"
            ),
            {"spirit_id": spirit_id},
        )
    ).all()
    return frozenset(str(row.topic_key) for row in rows)


async def load_recent_user_messages(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    *,
    now: datetime,
) -> tuple[PriorUserMessage, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT id, conversation_window_id, content FROM public.messages "
                "WHERE spirit_id = :spirit_id AND role = 'user' AND created_at >= :cutoff"
            ),
            {"spirit_id": spirit_id, "cutoff": now - timedelta(days=7)},
        )
    ).all()
    messages: list[PriorUserMessage] = []
    for row in rows:
        window_id = row.conversation_window_id or row.id
        messages.append(
            PriorUserMessage(id=row.id, window_id=window_id, content=str(row.content))
        )
    return tuple(messages)


async def sync_topic_masks(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    turns: tuple[ExtractSourceTurn, ...],
    drafts: list[object],
    *,
    now: datetime,
) -> None:
    users = tuple((turn.id, turn.content) for turn in turns if turn.role == "user")
    draft_rows = tuple(
        (draft.type, draft.normalized_topic_key, draft.summary, draft.source_quote)  # type: ignore[attr-defined]
        for draft in drafts
    )
    await _write_topic_masks(
        session,
        spirit_id,
        masked_topics(users, draft_rows) - remembered_topics(users, draft_rows),
        now,
    )
    await _clear_topic_masks(session, spirit_id, remembered_topics(users, draft_rows))


async def _write_topic_masks(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    topics: frozenset[str],
    now: datetime,
) -> None:
    for topic in topics:
        kind = topic.split(":", 1)[0]
        if kind not in {"preference", "knowledge", "emotion", "relationship"}:
            kind = "preference"
        event_id = uuid.uuid5(spirit_id, f"mask:{topic}")
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, tags, salience, confidence, status, "
                "topic_key, source_event_id, rules_version, confirm_count, "
                "last_confirmed_at, decision_audit, updated_at"
                ") VALUES ("
                ":id, :spirit_id, :memory_type, '不保存', '{}'::text[], 0, 1, 'superseded', "
                ":topic_key, :source_event_id, :rules_version, 1, :now, "
                "CAST(:audit AS jsonb), :now"
                ") ON CONFLICT (spirit_id, source_event_id) "
                "WHERE source_event_id IS NOT NULL DO UPDATE SET "
                "status = 'superseded', decision_audit = EXCLUDED.decision_audit, "
                "updated_at = EXCLUDED.updated_at"
            ),
            {
                "id": uuid.uuid4(),
                "spirit_id": spirit_id,
                "memory_type": kind,
                "topic_key": topic,
                "source_event_id": event_id,
                "rules_version": "kelin-v1-rules-1",
                "now": now,
                "audit": json.dumps(
                    {
                        "decision": "reject",
                        "rejection_code": "DO_NOT_STORE",
                        "event_ids": [str(event_id)],
                    },
                    ensure_ascii=False,
                ),
            },
        )


async def _clear_topic_masks(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    topics: frozenset[str],
) -> None:
    if not topics:
        return
    await session.execute(
        text(
            "UPDATE public.memories SET "
            "decision_audit = jsonb_set(decision_audit, '{rejection_code}', '\"CLEARED\"') "
            "WHERE spirit_id = :spirit_id AND topic_key = ANY(:topics) "
            "AND decision_audit->>'rejection_code' = 'DO_NOT_STORE'"
        ).bindparams(bindparam("topics", type_=ARRAY(Text()))),
        {"spirit_id": spirit_id, "topics": list(topics)},
    )


def _audit_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


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
