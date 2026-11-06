"""Rating settlement job shaped like the existing outbox insert.

The asker writes this row in the rating transaction. owner_id is that asker,
because the API outbox policy only allows inserting the caller's own owner_id.
The worker claims it with claim_due_outbox and event type craft.rating.settle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

CRAFT_RATING_SETTLE_EVENT = "craft.rating.settle"
CRAFT_RATING_AGGREGATE = "ask_question"
_POINTS = frozenset({0, 5, 10})
_OUTBOX_COLUMNS = (
    "aggregate_type",
    "aggregate_id",
    "event_type",
    "dedupe_key",
    "owner_id",
    "payload",
)


@dataclass(frozen=True, slots=True)
class CraftRatingSettleJob:
    aggregate_type: str
    aggregate_id: UUID
    event_type: str
    dedupe_key: str
    owner_id: UUID
    payload: dict[str, Any]

    def insert_columns(self) -> dict[str, Any]:
        return {
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "event_type": self.event_type,
            "dedupe_key": self.dedupe_key,
            "owner_id": self.owner_id,
            "payload": self.payload,
        }


def craft_rating_settle_job(
    *,
    asker_user_id: UUID,
    question_id: UUID,
    spirit_id: UUID,
    skill_slug: str,
    points: int,
) -> CraftRatingSettleJob:
    if points not in _POINTS:
        raise ValueError("rating points must be 0, 5, or 10")
    slug = skill_slug.strip()
    if not slug:
        raise ValueError("skill slug is required")
    return CraftRatingSettleJob(
        aggregate_type=CRAFT_RATING_AGGREGATE,
        aggregate_id=question_id,
        event_type=CRAFT_RATING_SETTLE_EVENT,
        dedupe_key=f"craft.rating.settle:{question_id}",
        owner_id=asker_user_id,
        payload={
            "ask_question_id": str(question_id),
            "spirit_id": str(spirit_id),
            "skill_slug": slug,
            "points": points,
        },
    )


def outbox_insert_columns() -> tuple[str, ...]:
    return _OUTBOX_COLUMNS
