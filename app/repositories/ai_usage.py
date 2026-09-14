"""Persist desensitized ai_usage rows. Spec §§6.4, 16.6, 18.1.

Column list is closed: no prompt, content, audio, or image payload.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ai_usage import (
    USAGE_ROLLUP_EVENT,
    BudgetThreshold,
    UsageSample,
    assert_metadata_payload,
    require_capability,
    rollup_dedupe_key,
)

INSERT_SQL = """
INSERT INTO public.ai_usage (
  user_id, request_id, capability, model_alias, prompt_version,
  input_units, output_units, audio_seconds, image_count,
  estimated_cost_micros, latency_ms, success, error_code,
  provider_request_id, created_at
) VALUES (
  :user_id, :request_id, :capability, :model_alias, :prompt_version,
  :input_units, :output_units, :audio_seconds, :image_count,
  :estimated_cost_micros, :latency_ms, :success, :error_code,
  :provider_request_id, :created_at
) RETURNING id
"""

DAY_SPENT_SQL = """
SELECT COALESCE(SUM(estimated_cost_micros), 0)
FROM public.ai_usage
WHERE user_id = :user_id
  AND ((created_at AT TIME ZONE 'UTC')::date) = :usage_date
"""

ROLLUP_SQL = "SELECT * FROM private.rollup_ai_usage(:usage_date)"
EMIT_ALERTS_SQL = """
SELECT private.emit_due_budget_alerts(
  :usage_date, :budget_micros, :warning_percent, :critical_percent
)
"""


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("ai_usage cannot write in a read-only transaction")


async def insert_usage(
    session: AsyncSession,
    owner_id: uuid.UUID,
    sample: UsageSample,
    *,
    estimated_cost_micros: int,
    now: datetime,
) -> int:
    require_capability(sample.capability)
    inserted = await session.scalar(
        text(INSERT_SQL),
        {
            "user_id": owner_id,
            "request_id": sample.request_id,
            "capability": sample.capability,
            "model_alias": sample.model_alias,
            "prompt_version": sample.prompt_version,
            "input_units": sample.input_units,
            "output_units": sample.output_units,
            "audio_seconds": sample.audio_seconds,
            "image_count": sample.image_count,
            "estimated_cost_micros": estimated_cost_micros,
            "latency_ms": sample.latency_ms,
            "success": sample.success,
            "error_code": sample.error_code,
            "provider_request_id": sample.provider_request_id,
            "created_at": now,
        },
    )
    if inserted is None:
        raise RuntimeError("ai_usage insert returned no id")
    return int(inserted)


async def day_spent_micros(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    usage_date: date,
) -> int:
    spent = await session.scalar(
        text(DAY_SPENT_SQL),
        {"user_id": owner_id, "usage_date": usage_date},
    )
    return int(spent or 0)


async def insert_budget_alert(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    usage_date: date,
    threshold: BudgetThreshold,
    spent_micros: int,
    budget_micros: int,
    now: datetime,
) -> bool:
    del now
    payload = {
        "threshold": threshold,
        "usage_date": usage_date.isoformat(),
        "estimated_cost_micros": spent_micros,
        "budget_micros": budget_micros,
    }
    assert_metadata_payload(payload)
    inserted = await session.scalar(
        text(
            "SELECT private.emit_owner_budget_alert("
            ":usage_date, :owner_id, :threshold, :spent_micros, :budget_micros)"
        ),
        {
            "usage_date": usage_date,
            "owner_id": owner_id,
            "threshold": threshold,
            "spent_micros": spent_micros,
            "budget_micros": budget_micros,
        },
    )
    return bool(inserted)


async def enqueue_usage_rollup(session: AsyncSession, *, usage_date: date, now: datetime) -> bool:
    payload = {"usage_date": usage_date.isoformat()}
    assert_metadata_payload(payload)
    inserted = await session.scalar(
        text(
            "INSERT INTO public.outbox_events ("
            "aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload, available_at"
            ") VALUES ("
            "'usage', :aggregate_id, :event_type, :dedupe_key, NULL, "
            "CAST(:payload AS jsonb), :now"
            ") ON CONFLICT (dedupe_key) DO NOTHING RETURNING id"
        ),
        {
            "aggregate_id": uuid.UUID("00000000-0000-4000-8000-000000000001"),
            "event_type": USAGE_ROLLUP_EVENT,
            "dedupe_key": rollup_dedupe_key(usage_date),
            "payload": json.dumps(payload),
            "now": now,
        },
    )
    return inserted is not None


async def claim_usage_rollup(
    session: AsyncSession, *, now: datetime, limit: int = 8
) -> tuple[tuple[uuid.UUID, date], ...]:
    rows = (
        await session.execute(
            text(
                "WITH picked AS ("
                "SELECT id FROM public.outbox_events "
                "WHERE event_type = :event_type "
                "AND status IN ('pending', 'retry') "
                "AND available_at <= :now "
                "ORDER BY available_at ASC, id ASC "
                "FOR UPDATE SKIP LOCKED "
                "LIMIT :limit"
                ") UPDATE public.outbox_events AS o "
                "SET status = 'claimed', locked_at = :now "
                "FROM picked WHERE o.id = picked.id "
                "RETURNING o.id, o.payload"
            ),
            {"event_type": USAGE_ROLLUP_EVENT, "now": now, "limit": limit},
        )
    ).all()
    claimed: list[tuple[uuid.UUID, date]] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else json.loads(str(row.payload))
        claimed.append((uuid.UUID(str(row.id)), date.fromisoformat(str(payload["usage_date"]))))
    return tuple(claimed)


async def mark_rollup_done(session: AsyncSession, *, job_id: uuid.UUID, now: datetime) -> bool:
    marked = await session.scalar(
        text(
            "UPDATE public.outbox_events SET status = 'done', processed_at = :now "
            "WHERE id = :id AND status IN ('pending', 'retry', 'claimed') "
            "RETURNING id"
        ),
        {"id": job_id, "now": now},
    )
    return marked is not None


async def rollup_ai_usage(session: AsyncSession, *, usage_date: date) -> tuple[int, int, int]:
    row = (await session.execute(text(ROLLUP_SQL), {"usage_date": usage_date})).one()
    return int(row.user_count), int(row.request_count), int(row.total_micros)


async def emit_due_budget_alerts(
    session: AsyncSession,
    *,
    usage_date: date,
    budget_micros: int,
    warning_percent: int,
    critical_percent: int,
) -> int:
    emitted = await session.scalar(
        text(EMIT_ALERTS_SQL),
        {
            "usage_date": usage_date,
            "budget_micros": budget_micros,
            "warning_percent": warning_percent,
            "critical_percent": critical_percent,
        },
    )
    return int(emitted or 0)
