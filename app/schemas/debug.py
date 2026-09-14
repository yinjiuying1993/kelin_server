"""Dev-only Debug API contract. Spec §14.10. extra=forbid; no user_id/SQL/URL/Secret."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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


class DebugTraitsPatch(_ForbidExtra):
    closeness: int | None = Field(default=None, ge=0, le=100)
    curiosity: int | None = Field(default=None, ge=0, le=100)
    sharpness: int | None = Field(default=None, ge=0, le=100)
    nocturnal: int | None = Field(default=None, ge=0, le=100)
    stubborn: int | None = Field(default=None, ge=0, le=100)


class DebugSpiritStateRequest(_ForbidExtra):
    status: SpiritStatus | None = None
    hunger: int | None = Field(default=None, ge=0, le=100)
    energy: int | None = Field(default=None, ge=0, le=100)
    mood: int | None = Field(default=None, ge=0, le=100)
    bond: int | None = Field(default=None, ge=0, le=100)
    traits: DebugTraitsPatch | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Self:
        if (
            self.status is None
            and self.hunger is None
            and self.energy is None
            and self.mood is None
            and self.bond is None
            and self.traits is None
        ):
            raise ValueError("debug spirit state requires a whitelist field")
        if self.traits is not None and not self.traits.model_fields_set:
            raise ValueError("debug traits requires a whitelist field")
        return self


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
