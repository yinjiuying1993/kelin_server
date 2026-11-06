"""The rating settlement job matches the existing outbox insert."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.domain.craft_ledger.settle_outbox import (
    CRAFT_RATING_SETTLE_EVENT,
    craft_rating_settle_job,
    outbox_insert_columns,
)

_ASKER = UUID("00000000-0000-4000-8000-000000000001")
_QUESTION = UUID("00000000-0000-4000-8000-000000000002")
_SPIRIT = UUID("00000000-0000-4000-8000-000000000003")


def test_job_uses_the_outbox_columns_and_hides_the_question() -> None:
    job = craft_rating_settle_job(
        asker_user_id=_ASKER,
        question_id=_QUESTION,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        points=0,
    )
    row = job.insert_columns()
    assert tuple(row) == outbox_insert_columns()
    assert row["event_type"] == CRAFT_RATING_SETTLE_EVENT
    assert row["aggregate_type"] == "ask_question"
    assert row["aggregate_id"] == _QUESTION
    assert row["owner_id"] == _ASKER
    assert row["dedupe_key"] == f"craft.rating.settle:{_QUESTION}"
    assert row["payload"]["points"] == 0
    assert "question" not in row["payload"]
    assert "body" not in row["payload"]


def test_same_dialogue_keeps_one_dedupe_key() -> None:
    first = craft_rating_settle_job(
        asker_user_id=_ASKER,
        question_id=_QUESTION,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        points=5,
    )
    second = craft_rating_settle_job(
        asker_user_id=_ASKER,
        question_id=_QUESTION,
        spirit_id=_SPIRIT,
        skill_slug="pour-tea",
        points=10,
    )
    assert first.dedupe_key == second.dedupe_key


def test_points_outside_the_rating_scale_are_rejected() -> None:
    with pytest.raises(ValueError):
        craft_rating_settle_job(
            asker_user_id=_ASKER,
            question_id=_QUESTION,
            spirit_id=_SPIRIT,
            skill_slug="pour-tea",
            points=7,
        )
