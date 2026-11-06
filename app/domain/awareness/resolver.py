"""settle(snapshot, facts, now). No IO, no wall clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from app.domain.awareness.departure import choose_departure
from app.domain.awareness.interaction import is_valid_interaction
from app.domain.awareness.life import apply_event_life, decay_life, life_band
from app.domain.awareness.lost_recall import food_recall_returns_home
from app.domain.awareness.statuses import letter_key_for
from app.domain.awareness.timeline import (
    VISIT_COOLDOWN,
    can_auto_depart_or_lose,
    idle_hours,
    require_aware,
    until_active,
)
from app.domain.awareness.types import (
    RULES_VERSION,
    AwarenessDecision,
    AwarenessFact,
    AwarenessSnapshot,
    SpiritStatus,
)


def _aware_optional(moment: datetime | None, *, field: str) -> datetime | None:
    if moment is None:
        return None
    return require_aware(moment, field=field)


def settle(
    snapshot: AwarenessSnapshot,
    facts: tuple[AwarenessFact, ...] = (),
    *,
    now: datetime,
) -> AwarenessDecision:
    now = require_aware(now, field="now")
    _aware_optional(snapshot.hatched_at, field="hatched_at")
    require_aware(snapshot.last_interact_at, field="last_interact_at")
    _aware_optional(snapshot.study_until, field="study_until")
    _aware_optional(snapshot.away_until, field="away_until")
    _aware_optional(snapshot.visit_cooldown_until, field="visit_cooldown_until")
    _aware_optional(snapshot.last_state_settled_at, field="last_state_settled_at")

    hunger, energy = decay_life(
        hunger=snapshot.hunger,
        energy=snapshot.energy,
        now=now,
        last_state_settled_at=snapshot.last_state_settled_at,
        nocturnal=snapshot.nocturnal,
        timezone=snapshot.timezone,
    )
    refresh = False
    food_recall = False
    for fact in facts:
        require_aware(fact.occurred_at, field="occurred_at")
        hunger, energy = apply_event_life(
            hunger, energy, fact.event_type, accepted=fact.accepted
        )
        if is_valid_interaction(fact):
            refresh = True
        if fact.food_recall_success:
            food_recall = True

    hatched = can_auto_depart_or_lose(snapshot)
    idle = idle_hours(now, snapshot.last_interact_at)
    status, study_until, away_until, cooldown, flags = _next_status(
        snapshot,
        now=now,
        idle=idle,
        hatched=hatched,
        refresh=refresh,
        food_recall=food_recall,
    )
    return AwarenessDecision(
        status=status,
        status_started_at=now if status != snapshot.status else None,
        study_until=study_until,
        away_until=away_until,
        visit_cooldown_until=cooldown,
        should_return_home=bool(flags["should_return_home"]),
        should_enter_lost=bool(flags["should_enter_lost"]),
        destination_plan_required=bool(flags["destination_plan_required"]),
        hunger=hunger,
        energy=energy,
        hunger_band=life_band(hunger),
        energy_band=life_band(energy),
        letter_key=letter_key_for(status),
        reason_code=str(flags["reason_code"]),
        valid_interaction_refresh=refresh,
        has_been_lost=bool(flags["has_been_lost"]),
        rules_version=RULES_VERSION,
        last_state_settled_at=now,
    )


def _next_status(
    snapshot: AwarenessSnapshot,
    *,
    now: datetime,
    idle: int,
    hatched: bool,
    refresh: bool,
    food_recall: bool,
) -> tuple[SpiritStatus, datetime | None, datetime | None, datetime | None, dict[str, Any]]:
    lost_flag = snapshot.has_been_lost
    cooldown = snapshot.visit_cooldown_until
    if food_recall_returns_home(snapshot.status, food_success=food_recall):
        return (
            "home",
            None,
            None,
            cooldown,
            _flags("PLAN_RECALL_HOME", lost=True, home=True),
        )
    if snapshot.status == "lost":
        snapshot = replace(snapshot, status="home")
    if not hatched:
        return (
            "home",
            None,
            None,
            cooldown,
            _flags("PLAN_UNHATCHED", lost=lost_flag),
        )
    if refresh and snapshot.status in {"study", "away"}:
        if snapshot.status == "away":
            cooldown = now + VISIT_COOLDOWN
        return (
            "home",
            None,
            None,
            cooldown,
            _flags("PLAN_INTERACTION_HOME", lost=lost_flag, home=True),
        )
    if snapshot.status == "study" and until_active(snapshot.study_until, now):
        return (
            "away",
            None,
            snapshot.study_until,
            cooldown,
            _flags("PLAN_AWAY", lost=lost_flag),
        )
    if snapshot.status == "away" and until_active(snapshot.away_until, now):
        return (
            "away",
            None,
            snapshot.away_until,
            cooldown,
            _flags("PLAN_AWAY", lost=lost_flag),
        )

    returned_from_away = snapshot.status == "away"
    if returned_from_away:
        cooldown = now + VISIT_COOLDOWN
        if idle < 18:
            return (
                "home",
                None,
                None,
                cooldown,
                _flags("PLAN_UNTIL_HOME", lost=lost_flag, home=True),
            )
    elif snapshot.status == "study" and idle < 18:
        return (
            "home",
            None,
            None,
            cooldown,
            _flags("PLAN_UNTIL_HOME", lost=lost_flag, home=True),
        )

    if idle >= 18:
        status, study_until, away_until, plan = choose_departure(
            snapshot, now, cooldown_until=cooldown
        )
        reason = {
            "away": "PLAN_AWAY",
            "home": "PLAN_COOLDOWN_HOME",
        }[status]
        return (
            status,
            study_until,
            away_until,
            cooldown,
            _flags(reason, lost=lost_flag, home=status == "home", plan=plan),
        )
    return (
        "home",
        None,
        None,
        cooldown,
        _flags("PLAN_HOME", lost=lost_flag),
    )


def _flags(
    reason: str,
    *,
    lost: bool,
    home: bool = False,
    enter_lost: bool = False,
    plan: bool = False,
) -> dict[str, Any]:
    return {
        "reason_code": reason,
        "has_been_lost": lost,
        "should_return_home": home,
        "should_enter_lost": enter_lost,
        "destination_plan_required": plan,
    }
