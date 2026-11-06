"""Persist one user-action fact and its quota. Rejection does not change the spirit."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.spirit_state import require_aware
from app.domain.user_actions.decide import decide_action
from app.domain.user_actions.quotas import quota_capability, quota_limit
from app.domain.user_actions.types import ActionDecision, ActionType, SpiritStatus, UserAction


async def record_user_action(
    session: AsyncSession,
    user: CurrentUser,
    *,
    spirit_id: uuid.UUID,
    action_type: ActionType,
    event_id: uuid.UUID,
    payload: dict[str, object],
    status: SpiritStatus,
    active_promise: bool,
    knowledge_moderated: bool,
    pact_accepted: bool,
    usage_date: date,
    timezone: str,
    now: datetime,
) -> ActionDecision:
    require_aware(now, field="now")
    await _lock_spirit(session, user.id, spirit_id)
    facts = {key: payload[key] for key in sorted(payload)}
    existing = await _load_event(session, spirit_id, event_id)
    if existing is not None:
        if existing["facts"] != facts or existing["action_type"] != action_type:
            raise ApiError("CONFLICT", public_error_message("CONFLICT"), status_code=409)
        return _decision_from_row(existing, replayed=True)
    used = await _lock_used(session, user.id, usage_date, action_type)
    decision = decide_action(
        UserAction(
            action_type=action_type,
            event_id=event_id,
            payload=payload,
            quota_used=used,
            status=status,
            active_promise=active_promise,
            knowledge_moderated=knowledge_moderated,
            pact_accepted=pact_accepted,
        )
    )
    await _insert_event(
        session,
        spirit_id=spirit_id,
        user_id=user.id,
        event_id=event_id,
        action_type=action_type,
        facts=facts,
        decision=decision,
        now=now,
    )
    if decision.accepted and decision.quota_cost > 0:
        await _spend_quota(
            session,
            user_id=user.id,
            spirit_id=spirit_id,
            usage_date=usage_date,
            timezone=timezone,
            action_type=action_type,
            used=used,
        )
    return decision


async def _lock_spirit(session: AsyncSession, user_id: uuid.UUID, spirit_id: uuid.UUID) -> None:
    found = await session.scalar(
        text(
            "SELECT id FROM public.spirits "
            "WHERE id = :spirit_id AND user_id = :user_id FOR UPDATE"
        ),
        {"spirit_id": spirit_id, "user_id": user_id},
    )
    if found is None:
        raise ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)


async def _load_event(
    session: AsyncSession, spirit_id: uuid.UUID, event_id: uuid.UUID
) -> dict[str, Any] | None:
    raw = await session.scalar(
        text(
            "SELECT payload FROM public.action_events "
            "WHERE spirit_id = :spirit_id AND event_id = :event_id"
        ),
        {"spirit_id": spirit_id, "event_id": event_id},
    )
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return None
    return raw


def _decision_from_row(raw: dict[str, Any], *, replayed: bool) -> ActionDecision:
    event_type = raw.get("event_type")
    code = raw.get("rejection_code")
    return ActionDecision(
        accepted=bool(raw.get("accepted")),
        event_type=None if event_type is None else str(event_type),
        quota_cost=0,
        rejection_code=None if code is None else str(code),
        valid_interaction=bool(raw.get("valid_interaction")),
        returns_home=bool(raw.get("returns_home")),
        replayed=replayed,
    )


async def _lock_used(
    session: AsyncSession,
    user_id: uuid.UUID,
    usage_date: date,
    action_type: str,
) -> int:
    capability = quota_capability(action_type)
    if capability is None:
        return 0
    used = await session.scalar(
        text(
            "SELECT used FROM public.action_quotas "
            "WHERE user_id = :user_id AND usage_date = :usage_date "
            "AND capability = :capability FOR UPDATE"
        ),
        {"user_id": user_id, "usage_date": usage_date, "capability": capability},
    )
    return int(used or 0)


async def _insert_event(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    user_id: uuid.UUID,
    event_id: uuid.UUID,
    action_type: str,
    facts: dict[str, object],
    decision: ActionDecision,
    now: datetime,
) -> None:
    body = {
        "action_type": action_type,
        "facts": facts,
        "accepted": decision.accepted,
        "event_type": decision.event_type,
        "rejection_code": decision.rejection_code,
        "valid_interaction": decision.valid_interaction,
        "returns_home": decision.returns_home,
    }
    kind = decision.event_type or action_type
    status = "accepted" if decision.accepted else "rejected"
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO public.action_events ("
                    "spirit_id, user_id, event_id, client_id, kind, status, payload, occurred_at"
                    ") VALUES ("
                    ":spirit_id, :user_id, :event_id, :event_id, :kind, :status, "
                    "CAST(:payload AS jsonb), :occurred_at)"
                ),
                {
                    "spirit_id": spirit_id,
                    "user_id": user_id,
                    "event_id": event_id,
                    "kind": kind,
                    "status": status,
                    "payload": json.dumps(body, separators=(",", ":"), sort_keys=True),
                    "occurred_at": now,
                },
            )
    except IntegrityError as exc:
        raise ApiError("CONFLICT", public_error_message("CONFLICT"), status_code=409) from exc


async def _spend_quota(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    spirit_id: uuid.UUID,
    usage_date: date,
    timezone: str,
    action_type: str,
    used: int,
) -> None:
    capability = quota_capability(action_type)
    limit = quota_limit(action_type)
    if capability is None or limit is None:
        return
    spent = await session.scalar(
        text(
            "INSERT INTO public.action_quotas ("
            "user_id, usage_date, capability, spirit_id, timezone, used, limit_value"
            ") VALUES ("
            ":user_id, :usage_date, :capability, :spirit_id, :timezone, :used, :limit_value"
            ") ON CONFLICT (user_id, usage_date, capability) DO UPDATE "
            "SET used = public.action_quotas.used + 1 "
            "WHERE public.action_quotas.used = :previous "
            "RETURNING used"
        ),
        {
            "user_id": user_id,
            "usage_date": usage_date,
            "capability": capability,
            "spirit_id": spirit_id,
            "timezone": timezone,
            "used": used + 1,
            "limit_value": limit,
            "previous": used,
        },
    )
    if spent is None:
        raise ApiError("QUOTA_EXCEEDED", public_error_message("QUOTA_EXCEEDED"), status_code=429)
