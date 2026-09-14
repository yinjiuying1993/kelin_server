"""Map recall settlement to public MutationResult. HTTP DTO only."""

from __future__ import annotations

from app.schemas.recall import RecallMutationResult, RecallPatch, RecallResource
from app.schemas.spirit import OnboardingState
from app.services.recall import RecallSettlement


def recall_result_from_settlement(settlement: RecallSettlement) -> RecallMutationResult:
    spirit = settlement.spirit
    return RecallMutationResult(
        resource=RecallResource(
            id=settlement.client_id,
            version=settlement.snapshot_version,
            method=settlement.method,
        ),
        patch=RecallPatch(
            snapshot_version=settlement.snapshot_version,
            spirit=spirit,
            room=settlement.room,
            onboarding=OnboardingState(
                required=spirit.onboarding_completed_at is None,
                step=spirit.onboarding_step,
                completed_at=spirit.onboarding_completed_at,
            ),
            memories_upsert=list(settlement.memories),
        ),
        quotas=list(settlement.quotas),
        events=list(settlement.events),
    )
