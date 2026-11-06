"""Chat turn facts. Bands only; no raw scores, weather, or catchphrases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ChatModeName = Literal[
    "safety",
    "recall_only",
    "away_note",
    "knowledge",
    "tired",
    "hungry",
    "companion",
]
StageName = Literal["whelp", "formed", "awake"]
StatusName = Literal["home", "away", "study", "lost"]
LifeBand = Literal["L0", "L1", "L2", "L3"]
TraitLevel = Literal["low", "mid", "high"]
TimeOfDay = Literal["day", "late_night"]
FirstDuty = Literal[
    "fixed_help",
    "guide_food",
    "state_whereabouts",
    "answer_evidence",
    "short_rest",
    "mention_food",
    "hold_user",
    "follow_emotion",
    "mention_leaving",
]


@dataclass(frozen=True, slots=True)
class TraitBands:
    closeness: TraitLevel = "mid"
    curiosity: TraitLevel = "mid"
    sharpness: TraitLevel = "mid"
    nocturnal: TraitLevel = "mid"
    stubborn: TraitLevel = "mid"


@dataclass(frozen=True, slots=True)
class Correction:
    topic: str
    claim: str
    new_value: str


@dataclass(frozen=True, slots=True)
class Overlay:
    address: str | None = None
    forbidden_addresses: tuple[str, ...] = ()
    corrections: tuple[Correction, ...] = ()
    blocked_topics: tuple[str, ...] = ()
    no_comfort: bool = False


@dataclass(frozen=True, slots=True)
class RecallItem:
    id: str
    summary: str
    active: bool = True
    use: str = "mention"
    memory_type: str = ""


@dataclass(frozen=True, slots=True)
class RecentTurn:
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class TurnPlan:
    mode: ChatModeName
    sentence_cap: int
    question_cap: int
    sting_cap: int
    first_duty: FirstDuty
    stage: StageName
    status: StatusName
    traits: TraitBands


@dataclass(frozen=True, slots=True)
class ModelReply:
    text: str
    intent: str
    citation_ids: tuple[str, ...] = ()
    evidence_kind: str | None = None
    safety: Literal["allow", "reject"] = "allow"
    urls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GateResult:
    ok: bool
    code: str | None
    text: str
    degraded: bool
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class Instruction:
    mode: ChatModeName
    sentence_cap: int
    question_cap: int
    sting_cap: int
    first_duty: FirstDuty
    status: StatusName
    stage: StageName
    hunger_band: LifeBand
    energy_band: LifeBand
    primary_label: str | None
    trait_bands: TraitBands
    overlay: Overlay
    recall: tuple[RecallItem, ...]
    recent_turns: tuple[RecentTurn, ...]
    time_of_day: TimeOfDay
    user_text: str
