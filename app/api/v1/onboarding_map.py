"""Map onboarding complete settlement to the public patch. HTTP DTO only."""

from __future__ import annotations

from app.api.v1.public_map import (
    hatched_at_public,
    room_public_assembled,
    spirit_public_from_mapping,
    utc_z,
)
from app.domain.onboarding import OnboardingCompleteSettlement
from app.schemas.onboarding import OnboardingCompletePatch, OnboardingCompleteResult
from app.schemas.spirit import MutationResource


def onboarding_complete_result_from_settlement(
    settlement: OnboardingCompleteSettlement,
) -> OnboardingCompleteResult:
    result = settlement.spirit
    hatched = hatched_at_public(hatched_at=result.hatched_at, created_at=result.created_at)
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
            "hatched_at": hatched,
            "created_at": utc_z(result.created_at),
        }
    )
    return OnboardingCompleteResult(
        resource=MutationResource(type="spirit", id=result.spirit_id, version=result.version),
        patch=OnboardingCompletePatch(
            snapshot_version=result.version,
            spirit=spirit,
            room=room_public_assembled(
                status=result.status,
                stage=result.stage,
                hunger=result.hunger,
                energy=result.energy,
                primary_emotion_label=None,
            ),
        ),
    )
