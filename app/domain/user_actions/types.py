"""User-action facts. No bond, mood, or hunger settlement."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

ActionType = Literal[
    "promise_create",
    "promise_complete",
    "promise_cancel",
    "pact_answer",
    "pact_complete",
    "recall",
]
SpiritStatus = Literal["home", "away", "study", "lost"]

FORBIDDEN_CLIENT_KEYS = frozenset(
    {
        "bond",
        "bond_delta",
        "stage",
        "mood",
        "mood_delta",
        "hunger",
        "hunger_delta",
        "energy",
        "energy_delta",
        "primary_label",
        "trait_delta",
        "closeness",
        "curiosity",
        "sharpness",
        "nocturnal",
        "stubborn",
        "scholar_marks",
    }
)


@dataclass(frozen=True, slots=True)
class UserAction:
    action_type: ActionType
    event_id: uuid.UUID
    payload: dict[str, object] = field(default_factory=dict)
    quota_used: int = 0
    status: SpiritStatus = "home"
    active_promise: bool = False
    knowledge_moderated: bool = False
    pact_accepted: bool = False
    settled_event_ids: frozenset[uuid.UUID] = frozenset()
    prior: ActionDecision | None = None


@dataclass(frozen=True, slots=True)
class ActionDecision:
    accepted: bool
    event_type: str | None
    quota_cost: int
    rejection_code: str | None
    valid_interaction: bool
    returns_home: bool
    replayed: bool = False
