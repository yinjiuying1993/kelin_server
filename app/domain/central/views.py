"""Read-only views central passes into modules. No IO, no repositories."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.domain.central.types import LifeBand, SpiritStatus


@dataclass(frozen=True, slots=True)
class AwarenessView:
    status: SpiritStatus
    hunger_band: LifeBand
    energy_band: LifeBand
    letter_candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EmotionView:
    primary_label: str | None
    realtime_emotion: str | None
    mood_band: str | None
    reply_budget: int | None


@dataclass(frozen=True, slots=True)
class GrowthView:
    stage: str
    stage_image_key: str | None
    trait_bands: tuple[str, ...]
    cognition_band: str | None


@dataclass(frozen=True, slots=True)
class RecallContext:
    memory_ids: tuple[uuid.UUID, ...]
    summaries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActiveMemoryStats:
    preference: int
    knowledge: int
    emotion: int
    relationship: int


@dataclass(frozen=True, slots=True)
class RoomProjectionView:
    stage_image_key: str | None
    primary_emotion_label: str | None
    letter_key: str | None
    text_status: str | None
    status: SpiritStatus
