"""Dev-only Debug API contract. Spec §14.10. extra=forbid; no user_id/SQL/URL/Secret."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.providers.types import MemoryType
from app.schemas.envelope import EnvelopeError
from app.schemas.memory import MemoryPublic, MemoryStatus
from app.schemas.spirit import MutationEvent, QuotaUsage, SpiritPublic, SpiritStatus

DebugFailureCapability = Literal[
    "chat",
    "extract",
    "asr",
    "tts",
    "vision",
    "safety",
    "search",
    "storage",
    "apns",
]
DebugFailureMode = Literal["success", "delay", "cancel", "error"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DebugSpiritStateRequest(_ForbidExtra):
    status: SpiritStatus


class DebugSpiritTimeRequest(_ForbidExtra):
    last_interact_at: AwareDatetime
    away_until: AwareDatetime | None = None
    study_until: AwareDatetime | None = None
    hatched_at: AwareDatetime | None = None


class DebugMemoryCreateRequest(_ForbidExtra):
    type: MemoryType
    summary: str = Field(min_length=1, max_length=500)
    tags: list[str] = Field(default_factory=list)
    salience: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    status: MemoryStatus = "active"


class DebugReportEligibilityRequest(_ForbidExtra):
    hatched_days_ago: int = Field(ge=0, le=3650)
    ordinary_dialogue_rounds: int = Field(ge=0, le=100000)


class DebugFailureRequest(_ForbidExtra):
    capability: DebugFailureCapability
    mode: DebugFailureMode
    remaining_calls: int = Field(ge=0, le=100)
    latency_ms: int | None = Field(default=None, ge=0, le=120000)


class DebugResetRequest(_ForbidExtra):
    confirm: Literal["RESET_MY_DEBUG_ACCOUNT"]


class DebugMutationPatch(_ForbidExtra):
    snapshot_version: int = Field(ge=1)
    spirit: SpiritPublic | None = None
    memories_upsert: list[MemoryPublic] = Field(default_factory=list)


class DebugMutationResult(_ForbidExtra):
    resource: dict[str, Any]
    patch: DebugMutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class DebugUsageSnapshot(_ForbidExtra):
    snapshot_version: int = Field(ge=1)
    quotas: list[QuotaUsage] = Field(default_factory=list)


class DebugSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: DebugMutationResult | DebugUsageSnapshot
    error: None = None
    request_id: str
    server_time: str


class DebugErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
