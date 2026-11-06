"""Central orchestrator types. No IO."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

RULES_VERSION = "kelin-v1-rules-1"

RequestKind = Literal["chat", "user_action", "memory_control", "clock_tick", "bootstrap"]
ChatMode = Literal[
    "safety",
    "recall_only",
    "away_note",
    "knowledge",
    "tired",
    "hungry",
    "companion",
]
SpiritStatus = Literal["home", "away", "study", "lost"]
LifeBand = Literal["L0", "L1", "L2", "L3"]
Consumer = Literal["awareness", "memory", "chat", "room"]
LockTarget = Literal[
    "spirit",
    "idempotency_record",
    "awareness",
    "memory",
    "projection",
]
PipelineStep = Literal[
    "idempotency",
    "lock_aggregate",
    "awareness_settle",
    "emotion_resolve",
    "choose_chat_mode",
    "memory_recall",
    "growth_context",
    "assemble_chat_input",
    "chat_generate",
    "persist_reply",
    "publish_dialogue_completed",
    "memory_window",
    "consume_accepted_facts",
    "user_action_validate",
    "persist_fact_and_quota",
    "persist_reject",
    "awareness_apply_side_effect",
    "emotion_consume",
    "memory_ingest",
    "growth_settle",
    "memory_control_apply",
    "familiarity_recompute",
    "growth_no_rollback",
    "emotion_drop_evidence",
    "project_room",
    "commit_snapshot",
]
IdempotencyOutcome = Literal["proceed", "replay", "conflict", "in_progress"]

REQUEST_KINDS: frozenset[RequestKind] = frozenset(
    {"chat", "user_action", "memory_control", "clock_tick", "bootstrap"}
)


@dataclass(frozen=True, slots=True)
class OrchestratorRequest:
    request_kind: RequestKind
    event_id: uuid.UUID
    spirit_id: uuid.UUID
    occurred_at: datetime
    client_payload_hash: str
    current_snapshot_version: int
    safety_flags: tuple[str, ...] = ()
    status: SpiritStatus = "home"
    hunger_band: LifeBand | None = None
    energy_band: LifeBand | None = None
    primary_emotion_label: str | None = None
    knowledge_question: bool = False
    action_event_type: str | None = None
    action_accepted: bool = True
    memory_cleared: bool = False


@dataclass(frozen=True, slots=True)
class OrchestratorPlan:
    request_kind: RequestKind
    pipeline: tuple[PipelineStep, ...]
    chat_mode: ChatMode | None
    allowed_consumers: tuple[Consumer, ...]
    projection_required: bool
    persist_reply_required: bool
    zero_side_effect: bool
    reason_code: str
    rules_version: str
    lock_order: tuple[LockTarget, ...]
    publish_dialogue_completed: bool
    bump_snapshot: bool
