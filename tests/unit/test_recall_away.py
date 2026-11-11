"""Calling an away spirit home. Clock settle must not hide an open trip."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.domain.awareness import AwarenessFact, AwarenessSnapshot, settle
from app.domain.recall import spirit_can_be_recalled

NOW = datetime(2026, 11, 11, 4, 0, tzinfo=UTC)


def test_home_without_a_trip_cannot_be_recalled() -> None:
    assert (
        spirit_can_be_recalled(
            awareness_status="home",
            spirit_status="home",
            visiting=False,
        )
        is False
    )


def test_spirit_row_or_open_visit_still_counts_after_awareness_returns() -> None:
    assert spirit_can_be_recalled(
        awareness_status="home",
        spirit_status="away",
        visiting=False,
    )
    assert spirit_can_be_recalled(
        awareness_status="home",
        spirit_status="home",
        visiting=True,
    )


def test_recall_fact_brings_an_away_spirit_home() -> None:
    snapshot = AwarenessSnapshot(
        status="away",
        hatched_at=NOW - timedelta(days=3),
        last_interact_at=NOW - timedelta(hours=2),
        study_until=None,
        away_until=NOW + timedelta(hours=2),
        visit_cooldown_until=None,
        has_unfinished_due_pact=False,
        has_been_lost=False,
        hunger=80,
        energy=80,
        last_state_settled_at=NOW - timedelta(hours=2),
    )
    decision = settle(
        snapshot,
        (
            AwarenessFact(
                event_id=uuid.uuid4(),
                event_type="recall.completed",
                occurred_at=NOW,
                valid_interaction=True,
                accepted=True,
                food_recall_success=True,
            ),
        ),
        now=NOW,
    )
    assert decision.status == "home"
    assert decision.away_until is None
