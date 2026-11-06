"""Shared public DTO adapters for V1 shapes. HTTP DTO only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.central.types import LifeBand
from app.domain.central.views import AwarenessView, EmotionView, GrowthView
from app.schemas.room import RoomPublic
from app.schemas.spirit import SpiritPublic, SpiritStatus
from app.services.room import assemble

DEFAULT_PRIMARY_EMOTION_LABEL = "平静"
PUBLIC_STAGE = "formed"
PUBLIC_STAGE_IMAGE = "formed_image"


def utc_z(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_z_optional(value: datetime | None) -> str | None:
    if value is None:
        return None
    return utc_z(value)


def hatched_at_public(*, hatched_at: datetime | None, created_at: datetime) -> str:
    if hatched_at is not None:
        return utc_z(hatched_at)
    return utc_z(created_at)


def spirit_public_from_mapping(payload: dict[str, Any]) -> SpiritPublic:
    hatched = payload.get("hatched_at")
    created = payload["created_at"]
    if not isinstance(created, str):
        created = utc_z(created)
    if hatched is None:
        hatched_text = created
    elif isinstance(hatched, str):
        hatched_text = hatched
    else:
        hatched_text = utc_z(hatched)
    return SpiritPublic.model_validate(
        {
            "id": payload["id"],
            "name": payload["name"],
            "egg": payload["egg"],
            "invite_code": payload["invite_code"],
            "stage": PUBLIC_STAGE,
            "status": _status(str(payload["status"])),
            "version": payload["version"],
            "hatched_at": hatched_text,
            "created_at": created,
        }
    )


def _band(value: int) -> LifeBand:
    n = max(0, min(100, value))
    if n <= 19:
        return "L0"
    if n <= 39:
        return "L1"
    if n <= 69:
        return "L2"
    return "L3"


def _status(value: str) -> SpiritStatus:
    if value == "study":
        return "away"
    if value == "lost":
        return "home"
    if value in {"home", "away"}:
        return value  # type: ignore[return-value]
    return "home"


def room_public_assembled(
    *,
    status: str,
    stage: str,
    hunger: int,
    energy: int,
    primary_emotion_label: str | None,
    letter_candidates: tuple[str, ...] = (),
    image_key: str | None = None,
) -> RoomPublic:
    _ = image_key
    view = assemble(
        awareness=AwarenessView(
            status=_status(status),
            hunger_band=_band(hunger),
            energy_band=_band(energy),
            letter_candidates=letter_candidates,
        ),
        emotion=EmotionView(
            primary_label=primary_emotion_label,
            realtime_emotion=None,
            mood_band=None,
            reply_budget=None,
        ),
        growth=GrowthView(
            stage=stage,
            stage_image_key=image_key,
            trait_bands=(),
            cognition_band=None,
        ),
    )
    text = view.text_status if view.text_status else view.status
    return RoomPublic(
        stage_image_key=PUBLIC_STAGE_IMAGE,
        letter_key=view.letter_key,
        text_status=text,
        status=view.status,
    )


def room_public_v1(
    *,
    stage: str,
    status: str,
    letter_key: str | None,
    primary_emotion_label: str | None,
) -> RoomPublic:
    """Explicit four-piece. Does not invent a letter when the caller passed none."""
    _ = (stage, primary_emotion_label)
    destination = _status(status)
    text = destination
    return RoomPublic(
        stage_image_key=PUBLIC_STAGE_IMAGE,
        letter_key=letter_key,
        text_status=text,
        status=destination,
    )


def home_room_public(*, stage: str = "whelp", status: str = "home") -> RoomPublic:
    return room_public_v1(
        stage=stage,
        status=status,
        letter_key=None,
        primary_emotion_label=DEFAULT_PRIMARY_EMOTION_LABEL,
    )
