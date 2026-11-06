"""S3-T04: lost returns home only via food recall.completed."""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.awareness import AwarenessFact, AwarenessSnapshot, settle
from app.domain.awareness.life import apply_event_life
from app.domain.awareness.types import SpiritStatus
from app.services.recall import recall_spirit

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
SERVICE = (
    Path(__file__).resolve().parents[2] / "app" / "services" / "recall.py"
).read_text(encoding="utf-8")


def _snap(*, status: SpiritStatus = "lost") -> AwarenessSnapshot:
    return AwarenessSnapshot(
        status=status,
        hatched_at=NOW - timedelta(days=3),
        last_interact_at=NOW - timedelta(hours=1),
        study_until=None,
        away_until=None,
        visit_cooldown_until=None,
        has_unfinished_due_pact=False,
        has_been_lost=True,
        hunger=80,
        energy=80,
        last_state_settled_at=NOW,
    )


def _fact(event_type: str, *, food_recall_success: bool = False) -> AwarenessFact:
    return AwarenessFact(
        event_id=uuid.uuid4(),
        event_type=event_type,
        occurred_at=NOW,
        valid_interaction=True,
        accepted=True,
        food_recall_success=food_recall_success,
    )


def test_recall_service_uses_food_recall_fact_not_sight() -> None:
    params = inspect.signature(recall_spirit).parameters
    assert "now" in params
    assert "method" not in params
    assert "recall.completed" in SERVICE
    assert "food_recall_success=True" in SERVICE
    assert "mark_returned_home" not in SERVICE
    assert "sight_memory" not in SERVICE
    assert "_try_consume_food" not in SERVICE
    assert "consume_quota" in SERVICE


def test_ordinary_food_feed_does_not_return_lost_home() -> None:
    fed = settle(_snap(), (_fact("feed.food.accepted"),), now=NOW)
    assert fed.status == "home"
    assert fed.has_been_lost is True
    assert fed.hunger == 100
    recalled = settle(
        _snap(),
        (_fact("recall.completed", food_recall_success=True),),
        now=NOW,
    )
    assert recalled.status == "home"
    assert recalled.has_been_lost is True
    assert recalled.hunger == 100
    assert recalled.energy == 90
    assert apply_event_life(80, 80, "recall.completed", accepted=True) == (100, 90)
