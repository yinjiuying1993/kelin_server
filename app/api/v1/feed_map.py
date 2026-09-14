"""Map feed settlement to public MutationResult. HTTP DTO only; no SQL."""

from __future__ import annotations

from typing import Literal, cast

from app.schemas.feed import FeedKind, FeedMutationResult, FeedPatch, FeedResource, FeedStatus
from app.schemas.spirit import OnboardingState
from app.services.feed import FeedSettlement

_PromiseStatus = Literal["active", "completed", "cancelled"]


def feed_result_from_settlement(settlement: FeedSettlement) -> FeedMutationResult:
    spirit = settlement.spirit
    raw_status = settlement.feed.promise_status
    promise_status: _PromiseStatus | None
    if raw_status in {"active", "completed", "cancelled"}:
        promise_status = cast(_PromiseStatus, raw_status)
    else:
        promise_status = None
    return FeedMutationResult(
        resource=FeedResource(
            id=settlement.feed.id,
            version=settlement.feed.version,
            kind=cast(FeedKind, settlement.feed.kind),
            status=cast(FeedStatus, settlement.feed.status),
            promise_status=promise_status,
        ),
        patch=FeedPatch(
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
