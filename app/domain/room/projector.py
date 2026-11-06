"""Assemble the homepage four-piece. Does not recompute source values."""

from __future__ import annotations

from app.domain.room.display import display_label
from app.domain.room.image_keys import stage_image_key
from app.domain.room.letters import pick_letter_key
from app.domain.room.text_status import text_status
from app.domain.room.types import ProjectionInput, RoomProjectionPatch


def project(source: ProjectionInput) -> RoomProjectionPatch:
    _ = source.rules_version
    if source.status == "study":
        status = "away"
    elif source.status == "lost":
        status = "home"
    else:
        status = source.status
    return RoomProjectionPatch(
        stage_image_key=stage_image_key(
            stage=source.stage, provided=source.stage_image_key
        ),
        primary_emotion_label=display_label(source.primary_emotion_label),
        letter_key=pick_letter_key(
            status=status, candidates=source.letter_candidates
        ),
        text_status=text_status(
            status=status,
            hunger_band=source.hunger_band,
            energy_band=source.energy_band,
            last_error=source.last_error,
            connectivity=source.connectivity,
        ),
        status=status,
        snapshot_version=source.snapshot_version,
    )
