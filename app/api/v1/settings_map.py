"""Map settings projection to SpiritPatchResult. HTTP DTO only."""

from __future__ import annotations

from datetime import datetime

from app.api.v1.public_map import (
    hatched_at_public,
    room_public_assembled,
    spirit_public_from_mapping,
    utc_z,
)
from app.repositories.settings import SettingsProjection
from app.schemas.settings import SpiritPatchPatch, SpiritPatchResult
from app.schemas.spirit import MutationResource, UserPreferencesPublic


def spirit_patch_result(projection: SettingsProjection, *, now: datetime) -> SpiritPatchResult:
    _ = now
    spirit = spirit_public_from_mapping(
        {
            "id": projection.spirit_id,
            "name": projection.name,
            "egg": projection.egg,
            "invite_code": projection.invite_code,
            "closeness": projection.closeness,
            "curiosity": projection.curiosity,
            "sharpness": projection.sharpness,
            "nocturnal": projection.nocturnal,
            "stubborn": projection.stubborn,
            "hunger": projection.hunger,
            "energy": projection.energy,
            "mood": projection.mood,
            "bond": projection.bond,
            "stage": projection.stage,
            "status": projection.status,
            "version": projection.version,
            "hatched_at": hatched_at_public(
                hatched_at=projection.hatched_at, created_at=projection.created_at
            ),
            "created_at": utc_z(projection.created_at),
        }
    )
    prefs = UserPreferencesPublic(
        tts_on=projection.tts_on,
        visit_on=projection.visit_on,
        timezone=projection.timezone,
        default_city=projection.default_city,
        location_weather_on=projection.location_weather_on,
        remote_search_on=projection.remote_search_on,
    )
    room = room_public_assembled(
        status=projection.status,
        stage=projection.stage,
        hunger=projection.hunger,
        energy=projection.energy,
        primary_emotion_label=None,
    )
    return SpiritPatchResult(
        resource=MutationResource(
            type="spirit", id=projection.spirit_id, version=projection.version
        ),
        patch=SpiritPatchPatch(
            snapshot_version=projection.version,
            spirit=spirit,
            preferences=prefs,
            room=room,
        ),
    )
