"""User-action entry. Feed, pact, and recall forward here."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.core.errors import ApiError, public_error_message
from app.domain.awareness.types import AwarenessFact
from app.domain.central import OrchestratorRequest
from app.domain.central.consumers import consumers_for_event
from app.domain.user_actions.types import ActionDecision, ActionType, SpiritStatus
from app.repositories.central.snapshot import LockedSpirit
from app.services.awareness import AwarenessSettleResult, settle_now
from app.services.central.facade import SettlementOutcome
from app.services.central.lifecycle import settle_then_resolve
from app.services.central.pipelines import run_user_action
from app.services.user_actions import record_user_action


async def publish_user_action(
    session: AsyncSession,
    user: CurrentUser,
    *,
    spirit_id: uuid.UUID,
    action_type: ActionType,
    event_type: str,
    event_id: uuid.UUID,
    payload: dict[str, object],
    status: SpiritStatus,
    active_promise: bool,
    knowledge_moderated: bool,
    pact_accepted: bool,
    usage_date: date,
    timezone: str,
    now: datetime,
    client_payload_hash: str,
    snapshot_version: int,
) -> ActionDecision:
    request = OrchestratorRequest(
        request_kind="user_action",
        event_id=event_id,
        spirit_id=spirit_id,
        occurred_at=now,
        client_payload_hash=client_payload_hash,
        current_snapshot_version=snapshot_version,
        action_event_type=event_type,
        status=status,
    )

    async def apply(inner: AsyncSession, _locked: LockedSpirit) -> SettlementOutcome:
        awareness = await settle_then_resolve(inner, user, now=now)
        decision = await record_user_action(
            inner,
            user,
            spirit_id=spirit_id,
            action_type=action_type,
            event_id=event_id,
            payload=payload,
            status=status,
            active_promise=active_promise,
            knowledge_moderated=knowledge_moderated,
            pact_accepted=pact_accepted,
            usage_date=usage_date,
            timezone=timezone,
            now=now,
        )
        changed = awareness.changed
        if decision.accepted and not decision.replayed:
            recalled = await _notify_accepted(
                inner,
                user,
                decision,
                event_id=event_id,
                now=now,
            )
            if recalled is not None:
                changed = changed or recalled.changed
        return SettlementOutcome(changed=changed, payload=_payload(decision))

    result = await run_user_action(session, user, request, legacy_settle=apply)
    return _decision(result.payload, replayed=result.replayed)


async def _notify_accepted(
    session: AsyncSession,
    user: CurrentUser,
    decision: ActionDecision,
    *,
    event_id: uuid.UUID,
    now: datetime,
) -> AwarenessSettleResult | None:
    """Accepted recall reaches awareness. Rejection never enters this function."""
    event_type = decision.event_type
    if event_type is None or "awareness" not in consumers_for_event(event_type):
        return None
    if event_type != "recall.completed":
        return None
    recalled = await settle_now(
        session,
        user,
        now=now,
        facts=(
            AwarenessFact(
                event_id=event_id,
                event_type=event_type,
                occurred_at=now,
                valid_interaction=decision.valid_interaction,
                accepted=True,
                food_recall_success=True,
            ),
        ),
    )
    if recalled.decision is None or recalled.decision.status != "home":
        raise ApiError(
            "SPIRIT_NOT_AWAY",
            public_error_message("SPIRIT_NOT_AWAY"),
            status_code=409,
        )
    return recalled


def _payload(decision: ActionDecision) -> dict[str, object]:
    return {
        "accepted": decision.accepted,
        "event_type": decision.event_type,
        "quota_cost": decision.quota_cost,
        "rejection_code": decision.rejection_code,
        "valid_interaction": decision.valid_interaction,
        "returns_home": decision.returns_home,
    }


def _decision(payload: dict[str, object], *, replayed: bool) -> ActionDecision:
    event_type = payload.get("event_type")
    code = payload.get("rejection_code")
    return ActionDecision(
        accepted=bool(payload.get("accepted")),
        event_type=None if event_type is None else str(event_type),
        quota_cost=0 if replayed else int(payload.get("quota_cost") or 0),
        rejection_code=None if code is None else str(code),
        valid_interaction=bool(payload.get("valid_interaction")),
        returns_home=bool(payload.get("returns_home")),
        replayed=replayed,
    )
