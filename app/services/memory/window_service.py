"""Seal extract windows and enqueue at most one async task. No model IO."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.domain.memory import EXTRACT_ROUNDS
from app.schemas.social import OutboxPayload

EXTRACT_EVENT = "extract.requested"
TURN_EXTRACT_EVENT = "extract.turn"
EXTRACT_AGGREGATE = "conversation_window"
TURN_AGGREGATE = "message"
ExtractTaskAction = Literal["skip", "seal_and_enqueue", "enqueue"]


@dataclass(frozen=True, slots=True)
class DialogueCompletedFact:
    event_id: uuid.UUID
    spirit_id: uuid.UUID
    conversation_window_id: uuid.UUID
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class WindowAdvanceResult:
    window_id: uuid.UUID
    status: str
    user_round_count: int
    task_created: bool


def extract_dedupe_key(window_id: uuid.UUID) -> str:
    return f"extract:window:{window_id}"


def extract_task_decision(
    *,
    onboarding: bool,
    status: str,
    user_round_count: int,
) -> ExtractTaskAction:
    if onboarding:
        return "skip"
    if status in {"extracting", "extracted"}:
        return "skip"
    if status == "open":
        if user_round_count < EXTRACT_ROUNDS:
            return "skip"
        return "seal_and_enqueue"
    if status in {"ready", "failed"}:
        return "enqueue"
    return "skip"


async def on_dialogue_completed(
    session: AsyncSession,
    user: CurrentUser,
    fact: DialogueCompletedFact,
) -> WindowAdvanceResult | None:
    row = (
        await session.execute(
            text(
                "SELECT id, status, onboarding, user_round_count "
                "FROM public.conversation_windows "
                "WHERE id = :window_id AND spirit_id = :spirit_id "
                "FOR UPDATE"
            ),
            {
                "window_id": fact.conversation_window_id,
                "spirit_id": fact.spirit_id,
            },
        )
    ).first()
    if row is None:
        return None
    await _enqueue_turn_judgment(
        session,
        owner_id=user.id,
        spirit_id=fact.spirit_id,
        window_id=row.id,
        available_at=fact.occurred_at,
    )
    status = str(row.status)
    action = extract_task_decision(
        onboarding=bool(row.onboarding),
        status=status,
        user_round_count=int(row.user_round_count),
    )
    if action == "skip":
        return WindowAdvanceResult(
            window_id=row.id,
            status=status,
            user_round_count=int(row.user_round_count),
            task_created=False,
        )
    if action == "seal_and_enqueue" and status == "open":
        await session.execute(
            text(
                "UPDATE public.conversation_windows SET status = 'ready' "
                "WHERE id = :window_id AND spirit_id = :spirit_id AND status = 'open' "
                "AND user_round_count >= :rounds"
            ),
            {
                "window_id": row.id,
                "spirit_id": fact.spirit_id,
                "rounds": EXTRACT_ROUNDS,
            },
        )
        status = "ready"
    created = await _enqueue_extract_task(
        session,
        owner_id=user.id,
        spirit_id=fact.spirit_id,
        window_id=row.id,
        available_at=fact.occurred_at,
    )
    return WindowAdvanceResult(
        window_id=row.id,
        status=status,
        user_round_count=int(row.user_round_count),
        task_created=created,
    )


async def _enqueue_turn_judgment(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    available_at: datetime,
) -> bool:
    """Queue one model judgment for the user line that just completed this chat."""
    message_id = await session.scalar(
        text(
            "SELECT id FROM public.messages "
            "WHERE spirit_id = :spirit_id AND conversation_window_id = :window_id "
            "AND role = 'user' "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        ),
        {"spirit_id": spirit_id, "window_id": window_id},
    )
    if message_id is None:
        return False
    payload = OutboxPayload(resource_id=message_id).model_dump(mode="json", exclude_none=True)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO public.outbox_events ("
                    "aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, "
                    "payload, available_at"
                    ") VALUES ("
                    ":aggregate_type, :aggregate_id, :event_type, :dedupe_key, :owner_id, "
                    "CAST(:payload AS jsonb), :available_at"
                    ")"
                ),
                {
                    "aggregate_type": TURN_AGGREGATE,
                    "aggregate_id": message_id,
                    "event_type": TURN_EXTRACT_EVENT,
                    "dedupe_key": f"extract:message:{message_id}",
                    "owner_id": owner_id,
                    "payload": json.dumps(payload),
                    "available_at": available_at,
                },
            )
    except IntegrityError:
        return False
    return True


async def _enqueue_extract_task(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    available_at: datetime,
) -> bool:
    captured = await session.scalar(
        text(
            "SELECT snapshot_version FROM public.spirits "
            "WHERE id = :spirit_id AND user_id = :owner_id"
        ),
        {"spirit_id": spirit_id, "owner_id": owner_id},
    )
    if captured is None:
        return False
    payload = OutboxPayload(
        resource_id=window_id,
        snapshot_version=int(captured),
    ).model_dump(mode="json", exclude_none=True)
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO public.outbox_events ("
                    "aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, "
                    "payload, available_at"
                    ") VALUES ("
                    ":aggregate_type, :aggregate_id, :event_type, :dedupe_key, :owner_id, "
                    "CAST(:payload AS jsonb), :available_at"
                    ")"
                ),
                {
                    "aggregate_type": EXTRACT_AGGREGATE,
                    "aggregate_id": window_id,
                    "event_type": EXTRACT_EVENT,
                    "dedupe_key": extract_dedupe_key(window_id),
                    "owner_id": owner_id,
                    "payload": json.dumps(payload),
                    "available_at": available_at,
                },
            )
    except IntegrityError:
        return False
    return True
