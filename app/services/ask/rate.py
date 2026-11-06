"""Submit one rating. Settlement stays on the outbox, not in this request."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.domain.craft_ledger.settle_outbox import craft_rating_settle_job
from app.repositories.ask_rating import RateTarget, StoredRating
from app.schemas.ask import AskRateResult, AskRating, points_for_rating


class RatingStore(Protocol):
    async def target(self, asker_user_id: UUID, question_id: UUID) -> RateTarget | None: ...

    async def find(self, asker_user_id: UUID, question_id: UUID) -> StoredRating | None: ...

    async def insert(
        self,
        *,
        asker_user_id: UUID,
        question_id: UUID,
        rating: str,
        points: int,
    ) -> bool: ...

    async def enqueue(self, job: object) -> None: ...


async def submit_rating(
    store: RatingStore,
    *,
    asker_user_id: UUID,
    question_id: UUID,
    client_id: UUID,
    rating: AskRating,
) -> AskRateResult:
    del client_id
    target = await store.target(asker_user_id, question_id)
    if target is None or not target.answered or target.spirit_owner_user_id == asker_user_id:
        raise _missing()
    existing = await store.find(asker_user_id, question_id)
    if existing is not None:
        return _result(question_id, existing.rating)
    points = points_for_rating(rating)
    inserted = await store.insert(
        asker_user_id=asker_user_id,
        question_id=question_id,
        rating=rating,
        points=points,
    )
    if not inserted:
        saved = await store.find(asker_user_id, question_id)
        if saved is None:
            raise _missing()
        return _result(question_id, saved.rating)
    await store.enqueue(
        craft_rating_settle_job(
            asker_user_id=asker_user_id,
            question_id=question_id,
            spirit_id=target.spirit_id,
            skill_slug=target.skill_slug,
            points=points,
        )
    )
    return _result(question_id, rating)


def _result(question_id: UUID, rating: str) -> AskRateResult:
    return AskRateResult(question_id=question_id, rating=rating)  # type: ignore[arg-type]


def _missing() -> ApiError:
    return ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)
