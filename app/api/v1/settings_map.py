"""Map settings projection to SpiritPatchResult. HTTP DTO only."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from app.api.v1.spirit_map import utc_z, utc_z_optional
from app.domain.bootstrap import DEFAULT_ROOM_WEATHER
from app.domain.room import project_room
from app.domain.settings import format_hhmm
from app.domain.spirit_state import SpiritStatus
from app.repositories.settings import SettingsProjection
from app.schemas.settings import SpiritPatchPatch, SpiritPatchResult
from app.schemas.spirit import MutationResource, SpiritPublic, UserPreferencesPublic
from app.services.bootstrap import room_public_from_projection


def spirit_patch_result(projection: SettingsProjection, *, now: datetime) -> SpiritPatchResult:
    completed = utc_z_optional(projection.onboarding_completed_at)
    spirit = SpiritPublic.model_validate(
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
            "scholar_marks": list(projection.scholar_marks),
            "version": projection.version,
            "onboarding_step": projection.onboarding_step,
            "onboarding_completed_at": completed,
            "hatched_at": utc_z_optional(projection.hatched_at),
            "created_at": utc_z(projection.created_at),
        }
    )
    prefs = UserPreferencesPublic(
        tts_on=projection.tts_on,
        push_on=projection.push_on,
        visit_on=projection.visit_on,
        dnd_start=format_hhmm(projection.dnd_start),
        dnd_end=format_hhmm(projection.dnd_end),
        timezone=projection.timezone,
        default_city=projection.default_city,
        location_weather_on=projection.location_weather_on,
        remote_search_on=projection.remote_search_on,
    )
    interacted = projection.last_interact_at or projection.created_at
    room = room_public_from_projection(
        project_room(
            status=cast(SpiritStatus, projection.status),
            spirit_id=projection.spirit_id,
            last_interact_at=interacted,
            now=now,
            scholar_marks=projection.scholar_marks,
            due_promise=None,
            pact_recap=None,
            unread_postcard=None,
            pending_sight=False,
            unread_footprint_count=0,
        ),
        weather=DEFAULT_ROOM_WEATHER,
        pending_sight=None,
        updated_at=projection.updated_at,
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
