"""Awareness domain types. No IO."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

RULES_VERSION = "kelin-v1-rules-1"
SpiritStatus = Literal["home", "study", "away", "lost"]
LifeBand = Literal["L0", "L1", "L2", "L3", "L4"]


@dataclass(frozen=True, slots=True)
class AwarenessSnapshot:
    status: SpiritStatus
    hatched_at: datetime | None
    last_interact_at: datetime
    study_until: datetime | None
    away_until: datetime | None
    visit_cooldown_until: datetime | None
    has_unfinished_due_pact: bool
    has_been_lost: bool
    hunger: int
    energy: int
    last_state_settled_at: datetime | None
    timezone: str = "Asia/Shanghai"
    nocturnal: int = 50


@dataclass(frozen=True, slots=True)
class AwarenessFact:
    event_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    valid_interaction: bool
    accepted: bool = True
    food_recall_success: bool = False


@dataclass(frozen=True, slots=True)
class AwarenessDecision:
    status: SpiritStatus
    status_started_at: datetime | None
    study_until: datetime | None
    away_until: datetime | None
    visit_cooldown_until: datetime | None
    should_return_home: bool
    should_enter_lost: bool
    destination_plan_required: bool
    hunger: int
    energy: int
    hunger_band: LifeBand
    energy_band: LifeBand
    letter_key: str | None
    reason_code: str
    valid_interaction_refresh: bool
    has_been_lost: bool
    rules_version: str
    last_state_settled_at: datetime
