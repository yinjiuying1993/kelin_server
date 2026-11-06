"""Room four-piece input and patch. No IO."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.central.types import LifeBand, SpiritStatus

StageImageKey = Literal["whelp_image", "formed_image", "awake_image"]


@dataclass(frozen=True, slots=True)
class ProjectionInput:
    stage: str
    primary_emotion_label: str | None
    status: SpiritStatus
    hunger_band: LifeBand
    energy_band: LifeBand
    letter_candidates: tuple[str, ...] = ()
    stage_image_key: str | None = None
    last_error: str | None = None
    connectivity: str | None = None
    snapshot_version: int = 1
    rules_version: str = "kelin-v1-rules-1"


@dataclass(frozen=True, slots=True)
class RoomProjectionPatch:
    stage_image_key: StageImageKey
    primary_emotion_label: str | None
    letter_key: str | None
    text_status: str
    status: SpiritStatus
    snapshot_version: int
