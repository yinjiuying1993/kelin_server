"""Map growth stage to the three image keys. Does not recompute stage."""

from __future__ import annotations

from app.domain.room.types import StageImageKey

STAGE_IMAGE_KEYS: dict[str, StageImageKey] = {
    "whelp": "whelp_image",
    "formed": "formed_image",
    "awake": "awake_image",
}
VALID_IMAGE_KEYS: frozenset[str] = frozenset(STAGE_IMAGE_KEYS.values())


def stage_image_key(*, stage: str, provided: str | None) -> StageImageKey:
    if provided in VALID_IMAGE_KEYS:
        return provided  # type: ignore[return-value]
    return STAGE_IMAGE_KEYS.get(stage, "whelp_image")
