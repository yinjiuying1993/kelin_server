"""Persist awareness settle into spirit_awareness. Life columns stay off the wide spirits row."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.domain.awareness import AwarenessDecision, AwarenessFact, settle
from app.domain.awareness.types import SpiritStatus
from app.repositories import awareness as awareness_repo


@dataclass(frozen=True, slots=True)
class AwarenessSettleResult:
    spirit_id: uuid.UUID | None
    decision: AwarenessDecision | None
    changed: bool
    destination_changed: bool
    last_interact_at: datetime | None
    previous_status: SpiritStatus | None


async def settle_now(
    session: AsyncSession,
    user: CurrentUser,
    *,
    now: datetime,
    facts: tuple[AwarenessFact, ...] = (),
    nocturnal: int | None = None,
    timezone: str | None = None,
    has_unfinished_due_pact: bool | None = None,
) -> AwarenessSettleResult:
    """Write status and timestamps."""
    await awareness_repo.assert_writable_transaction(session)
    locked = await awareness_repo.lock_awareness_for_owner(
        session,
        user.id,
        nocturnal=nocturnal,
        timezone=timezone,
        has_unfinished_due_pact=has_unfinished_due_pact,
    )
    if locked is None:
        return AwarenessSettleResult(
            spirit_id=None,
            decision=None,
            changed=False,
            destination_changed=False,
            last_interact_at=None,
            previous_status=None,
        )
    decision = settle(locked.snapshot, facts, now=now)
    last_interact_at = (
        now if decision.valid_interaction_refresh else locked.snapshot.last_interact_at
    )
    destination_changed = (
        decision.status != locked.snapshot.status
        or decision.study_until != locked.snapshot.study_until
        or decision.away_until != locked.snapshot.away_until
        or decision.visit_cooldown_until != locked.snapshot.visit_cooldown_until
        or decision.has_been_lost != locked.snapshot.has_been_lost
    )
    changed = destination_changed or last_interact_at != locked.snapshot.last_interact_at
    await awareness_repo.apply_awareness_decision(
        session,
        locked.spirit_id,
        status=decision.status,
        last_interact_at=last_interact_at,
        study_until=decision.study_until,
        away_until=decision.away_until,
        visit_cooldown_until=decision.visit_cooldown_until,
        has_been_lost=decision.has_been_lost,
        last_state_settled_at=decision.last_state_settled_at,
    )
    return AwarenessSettleResult(
        spirit_id=locked.spirit_id,
        decision=decision,
        changed=changed,
        destination_changed=destination_changed,
        last_interact_at=last_interact_at,
        previous_status=locked.snapshot.status,
    )
