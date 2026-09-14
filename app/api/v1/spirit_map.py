"""Map create results to public MutationResult. HTTP DTO only; no SQL or traits."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.spirit import SpiritCreateResult
from app.schemas.spirit import (
    MutationPatch,
    MutationResource,
    MutationResult,
    OnboardingState,
    SpiritPublic,
)


def utc_z(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_z_optional(value: datetime | None) -> str | None:
    if value is None:
        return None
    return utc_z(value)


def mutation_result_from_create(result: SpiritCreateResult) -> MutationResult:
    completed = utc_z_optional(result.onboarding_completed_at)
    spirit = SpiritPublic.model_validate(
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
            "scholar_marks": list(result.scholar_marks),
            "version": result.version,
            "onboarding_step": result.onboarding_step,
            "onboarding_completed_at": completed,
            "hatched_at": utc_z_optional(result.hatched_at),
            "created_at": utc_z(result.created_at),
        }
    )
    return MutationResult(
        resource=MutationResource(type="spirit", id=result.spirit_id, version=result.version),
        patch=MutationPatch(
            snapshot_version=result.version,
            spirit=spirit,
            preferences=None,
            onboarding=OnboardingState(
                required=True,
                step=result.onboarding_step,
                completed_at=completed,
            ),
        ),
    )
