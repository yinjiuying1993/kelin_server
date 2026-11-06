"""Map recall settlement to public MutationResult. HTTP DTO only."""

from __future__ import annotations

from app.schemas.recall import RecallMutationResult, RecallPatch, RecallResource
from app.services.recall import RecallSettlement


def recall_result_from_settlement(settlement: RecallSettlement) -> RecallMutationResult:
    return RecallMutationResult(
        resource=RecallResource(
            id=settlement.client_id,
            version=settlement.snapshot_version,
            method=settlement.method,
        ),
        patch=RecallPatch(
            snapshot_version=settlement.snapshot_version,
            spirit=settlement.spirit,
            room=settlement.room,
            memories_upsert=list(settlement.memories),
        ),
        quotas=list(settlement.quotas),
        events=list(settlement.events),
    )
