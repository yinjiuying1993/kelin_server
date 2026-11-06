"""V3-S7-T03 one rating is stored and settlement is only queued."""

from __future__ import annotations

import inspect
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.domain.craft_ledger.settle_outbox import CRAFT_RATING_SETTLE_EVENT
from app.repositories import ask_rating as rating_sql
from app.repositories.ask_rating import RateTarget, StoredRating
from app.services.ask.rate import submit_rating

_ASKER = UUID("00000000-0000-4000-8000-0000000000c1")
_OWNER = UUID("00000000-0000-4000-8000-0000000000c2")
_SPIRIT = UUID("00000000-0000-4000-8000-0000000000c3")
_QUESTION = UUID("00000000-0000-4000-8000-0000000000c4")
_CLIENT = UUID("00000000-0000-4000-8000-0000000000c5")
_OTHER = UUID("00000000-0000-4000-8000-0000000000c6")
_LEDGER = {"help_count": 4, "total_points": 20, "rank_name": "门边学徒"}


def _target(*, owner: UUID = _OWNER, answered: bool = True) -> RateTarget:
    return RateTarget(_SPIRIT, owner, "pour-tea", answered)


class _Store:
    def __init__(self, target: RateTarget | None) -> None:
        self._target = target
        self.saved: StoredRating | None = None
        self.jobs: list[object] = []
        self.ledger = dict(_LEDGER)

    async def target(self, asker_user_id: UUID, question_id: UUID) -> RateTarget | None:
        del asker_user_id, question_id
        return self._target

    async def find(self, asker_user_id: UUID, question_id: UUID) -> StoredRating | None:
        del asker_user_id, question_id
        return self.saved

    async def insert(
        self,
        *,
        asker_user_id: UUID,
        question_id: UUID,
        rating: str,
        points: int,
    ) -> bool:
        del asker_user_id, question_id
        if self.saved is not None:
            return False
        self.saved = StoredRating(rating, points)
        return True

    async def enqueue(self, job: object) -> None:
        self.jobs.append(job)


@pytest.mark.asyncio
async def test_repeat_rating_returns_the_first_and_does_not_add_points() -> None:
    store = _Store(_target())
    first = await submit_rating(
        store,
        asker_user_id=_ASKER,
        question_id=_QUESTION,
        client_id=_CLIENT,
        rating="不满意",
    )
    assert first.rating == "不满意"
    assert set(first.model_dump()) == {"question_id", "rating"}
    assert store.saved is not None
    assert store.saved.points == 0
    assert len(store.jobs) == 1
    job = store.jobs[0]
    assert job.event_type == CRAFT_RATING_SETTLE_EVENT
    assert job.owner_id == _ASKER
    assert job.dedupe_key == f"craft.rating.settle:{_QUESTION}"
    assert job.payload["points"] == 0
    assert job.payload["skill_slug"] == "pour-tea"
    again = await submit_rating(
        store,
        asker_user_id=_ASKER,
        question_id=_QUESTION,
        client_id=_OTHER,
        rating="非常满意",
    )
    assert again.rating == "不满意"
    assert store.saved.points == 0
    assert len(store.jobs) == 1
    assert store.ledger == _LEDGER


@pytest.mark.asyncio
async def test_missing_unanswered_and_own_spirit_are_not_rated() -> None:
    for target in (None, _target(answered=False), _target(owner=_ASKER)):
        store = _Store(target)
        with pytest.raises(ApiError) as caught:
            await submit_rating(
                store,
                asker_user_id=_ASKER,
                question_id=_QUESTION,
                client_id=_CLIENT,
                rating="满意",
            )
        assert caught.value.code == "NOT_FOUND"
        assert caught.value.message == "not found"
        assert caught.value.status_code == 404
        assert store.saved is None
        assert store.jobs == []
        assert store.ledger == _LEDGER


def test_rating_request_does_not_write_the_ledger() -> None:
    source = inspect.getsource(rating_sql) + inspect.getsource(submit_rating)
    for forbidden in ("craft_ledgers", "help_count", "total_points", "rank_for_points"):
        assert forbidden not in source
