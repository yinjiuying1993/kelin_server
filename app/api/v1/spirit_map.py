"""Map create results to public MutationResult. HTTP DTO only; no SQL or traits."""

from __future__ import annotations

from app.api.v1.public_map import (
    hatched_at_public,
    home_room_public,
    spirit_public_from_mapping,
    utc_z,
    utc_z_optional,
)
from app.domain.spirit import SpiritCreateResult
from app.schemas.spirit import MutationPatch, MutationResource, MutationResult

__all__ = ["mutation_result_from_create", "utc_z", "utc_z_optional"]


def mutation_result_from_create(result: SpiritCreateResult) -> MutationResult:
    spirit = spirit_public_from_mapping(
        {
            "id": result.spirit_id,
            "name": result.name,
            "egg": result.egg,
            "invite_code": result.invite_code,
            "closeness": result.closeness,
            "curiosity": result.curiosity,
            "sharpness": result.sharpness,
            "nocturnal": result.nocturnal,
            "stubborn": result.stubborn,
            "hunger": result.hunger,
            "energy": result.energy,
            "mood": result.mood,
            "bond": result.bond,
            "stage": result.stage,
            "status": result.status,
            "version": result.version,
            "hatched_at": hatched_at_public(
                hatched_at=result.hatched_at, created_at=result.created_at
            ),
            "created_at": utc_z(result.created_at),
        }
    )
    return MutationResult(
        resource=MutationResource(type="spirit", id=result.spirit_id, version=result.version),
        patch=MutationPatch(
            snapshot_version=result.version,
            spirit=spirit,
            room=home_room_public(stage=result.stage, status=result.status),
            preferences=None,
        ),
    )
