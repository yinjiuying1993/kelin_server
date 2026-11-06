"""Assemble RoomProjectionView from central Views. No peer-module IO."""

from __future__ import annotations

from app.domain.central.views import (
    AwarenessView,
    EmotionView,
    GrowthView,
    RoomProjectionView,
)
from app.domain.room import ProjectionInput, project


def assemble(
    *,
    awareness: AwarenessView,
    emotion: EmotionView,
    growth: GrowthView,
    last_error: str | None = None,
    connectivity: str | None = None,
    snapshot_version: int = 1,
) -> RoomProjectionView:
    _ = emotion.realtime_emotion
    patch = project(
        ProjectionInput(
            stage=growth.stage,
            primary_emotion_label=emotion.primary_label,
            status=awareness.status,
            hunger_band=awareness.hunger_band,
            energy_band=awareness.energy_band,
            letter_candidates=awareness.letter_candidates,
            stage_image_key=growth.stage_image_key,
            last_error=last_error,
            connectivity=connectivity,
            snapshot_version=snapshot_version,
        )
    )
    return RoomProjectionView(
        stage_image_key=patch.stage_image_key,
        primary_emotion_label=patch.primary_emotion_label,
        letter_key=patch.letter_key,
        text_status=patch.text_status,
        status=patch.status,
    )
