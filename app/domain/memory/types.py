"""Memory domain types. No IO."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.memory.constants import RULES_VERSION

MemoryType = Literal["preference", "knowledge", "emotion", "relationship"]
MemorySignal = Literal["explicit", "repeated", "commitment", "teachable", "stable_emotion"]
MemoryStatus = Literal["active", "rejected", "sealed", "deleted", "superseded", "stale"]
DecisionKind = Literal["accept", "reject", "merge", "supersede"]
EvidenceRole = Literal["user", "spirit", "event"]


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: uuid.UUID
    memory_type: str
    summary: str
    normalized_topic_key: str
    source_event_id: uuid.UUID
    source_message_ids: tuple[uuid.UUID, ...]
    source_quote: str
    confidence: float
    signal: str
    evidence_role: EvidenceRole
    occurred_at: datetime
    repeat_count: int = 1
    supporting_windows: int = 1
    event_type: str | None = None
    do_not_store: bool = False
    is_joke_or_roleplay: bool = False
    user_emphasis: bool = False
    has_explicit_date: bool = False
    has_uncertainty_word: bool = False
    replaces_existing: bool = False
    re_remember: bool = False
    from_search: bool = False
    is_inference: bool = False
    is_medical: bool = False
    is_third_party: bool = False


@dataclass(frozen=True, slots=True)
class MemoryIndexItem:
    memory_id: uuid.UUID
    memory_type: str
    topic_key: str
    summary: str
    status: MemoryStatus
    salience: int
    confidence: float
    confirm_count: int
    last_confirmed_at: datetime
    source_event_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AdmitContext:
    now: datetime
    user_message_ids: frozenset[uuid.UUID]
    do_not_store_topics: frozenset[str]
    index: tuple[MemoryIndexItem, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryDecision:
    decision: DecisionKind
    candidate_id: uuid.UUID
    memory_type: str | None
    normalized_topic_key: str | None
    summary: str | None
    confidence: float | None
    salience: int | None
    source_event_id: uuid.UUID
    source_message_ids: tuple[uuid.UUID, ...]
    conflict_target_id: uuid.UUID | None
    rejection_code: str | None
    confirm_delta: int
    rules_version: str = RULES_VERSION


@dataclass(frozen=True, slots=True)
class RecallItem:
    memory_id: uuid.UUID
    topic_key: str
    summary: str
    status: str
    salience: int
    last_confirmed_at: datetime
    topic_score: int
    forced: bool = False
