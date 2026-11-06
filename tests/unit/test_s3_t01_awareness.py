"""S3-T01 awareness time and life-state pure functions."""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.awareness import (
    AwarenessDecision,
    AwarenessFact,
    AwarenessSnapshot,
    decay_life,
    food_recall_returns_home,
    idle_hours,
    settle,
)
from app.domain.awareness.life import apply_event_life, life_band
from app.domain.awareness.types import SpiritStatus
from pytest import raises

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
HATCHED = NOW - timedelta(days=3)


def _snap(
    *,
    status: SpiritStatus = "home",
    idle: timedelta = timedelta(hours=1),
    hatched_at: datetime | None = HATCHED,
    study_until: datetime | None = None,
    away_until: datetime | None = None,
    visit_cooldown_until: datetime | None = None,
    has_unfinished_due_pact: bool = False,
    has_been_lost: bool = False,
    hunger: int = 80,
    energy: int = 80,
    last_state_settled_at: datetime | None = NOW,
    nocturnal: int = 50,
) -> AwarenessSnapshot:
    return AwarenessSnapshot(
        status=status,
        hatched_at=hatched_at,
        last_interact_at=NOW - idle,
        study_until=study_until,
        away_until=away_until,
        visit_cooldown_until=visit_cooldown_until,
        has_unfinished_due_pact=has_unfinished_due_pact,
        has_been_lost=has_been_lost,
        hunger=hunger,
        energy=energy,
        last_state_settled_at=last_state_settled_at,
        nocturnal=nocturnal,
    )


def _fact(
    event_type: str,
    *,
    valid: bool = True,
    accepted: bool = True,
    food_recall_success: bool = False,
) -> AwarenessFact:
    return AwarenessFact(
        event_id=uuid.uuid4(),
        event_type=event_type,
        occurred_at=NOW,
        valid_interaction=valid,
        accepted=accepted,
        food_recall_success=food_recall_success,
    )


def test_idle_1759_stays_home_1800_can_leave() -> None:
    under = settle(_snap(idle=timedelta(hours=17, minutes=59)), now=NOW)
    assert under.status == "home"
    assert idle_hours(NOW, NOW - timedelta(hours=17, minutes=59)) == 17
    at_18 = settle(_snap(idle=timedelta(hours=18)), now=NOW)
    assert at_18.status == "away"
    assert at_18.away_until == NOW + timedelta(hours=3)
    assert at_18.destination_plan_required is True


def test_unhatched_never_enters_study_away_or_lost() -> None:
    for idle in (timedelta(hours=18), timedelta(hours=72)):
        decision = settle(_snap(idle=idle, hatched_at=None), now=NOW)
        assert decision.status == "home"
        assert decision.should_enter_lost is False
        assert decision.reason_code == "PLAN_UNHATCHED"


def test_hatched_immediately_allows_18_and_72() -> None:
    just_hatched = NOW - timedelta(seconds=1)
    away = settle(
        _snap(idle=timedelta(hours=18), hatched_at=just_hatched),
        now=NOW,
    )
    assert away.status == "away"
    still_visiting = settle(
        _snap(idle=timedelta(hours=72), hatched_at=just_hatched),
        now=NOW,
    )
    assert still_visiting.status == "away"
    assert still_visiting.should_enter_lost is False
    assert still_visiting.has_been_lost is False


def test_due_pact_goes_away_not_study() -> None:
    away = settle(
        _snap(idle=timedelta(hours=18), has_unfinished_due_pact=True),
        now=NOW,
    )
    assert away.status == "away"
    assert away.study_until is None
    assert away.away_until == NOW + timedelta(hours=3)


def test_72h_stays_home_or_visiting() -> None:
    home = settle(_snap(status="home", idle=timedelta(hours=72)), now=NOW)
    assert home.status == "away"
    assert home.letter_key == "away"
    study = settle(_snap(status="study", idle=timedelta(hours=72)), now=NOW)
    assert study.status == "away"
    away = settle(_snap(status="away", idle=timedelta(hours=72)), now=NOW)
    assert away.status == "home"
    assert away.letter_key is None


def test_study_until_then_home_away_cooldown_blocks_repeat() -> None:
    kept = settle(
        _snap(
            status="study",
            idle=timedelta(hours=1),
            study_until=NOW + timedelta(hours=1),
        ),
        now=NOW,
    )
    assert kept.status == "away"
    assert kept.study_until is None
    assert kept.away_until == NOW + timedelta(hours=1)
    expired = settle(
        _snap(
            status="study",
            idle=timedelta(hours=1),
            study_until=NOW,
        ),
        now=NOW,
    )
    assert expired.status == "home"
    blocked = settle(
        _snap(
            idle=timedelta(hours=18),
            visit_cooldown_until=NOW + timedelta(hours=6),
        ),
        now=NOW,
    )
    assert blocked.status == "home"
    assert blocked.reason_code == "PLAN_COOLDOWN_HOME"


def test_away_until_expiry_sets_cooldown_and_does_not_leave_again() -> None:
    decision = settle(
        _snap(
            status="away",
            idle=timedelta(hours=20),
            away_until=NOW,
        ),
        now=NOW,
    )
    assert decision.status == "home"
    assert decision.visit_cooldown_until == NOW + timedelta(hours=12)


def test_valid_interaction_returns_from_away() -> None:
    decision = settle(
        _snap(status="away", idle=timedelta(hours=1), away_until=NOW + timedelta(hours=2)),
        (_fact("dialogue.completed"),),
        now=NOW,
    )
    assert decision.status == "home"
    assert decision.valid_interaction_refresh is True
    assert decision.visit_cooldown_until == NOW + timedelta(hours=12)


def test_bootstrap_does_not_refresh_interaction() -> None:
    decision = settle(
        _snap(idle=timedelta(hours=1)),
        (_fact("bootstrap", valid=False),),
        now=NOW,
    )
    assert decision.status == "home"
    assert decision.valid_interaction_refresh is False


def test_former_lost_returns_home_without_a_lost_status() -> None:
    knowledge = settle(
        _snap(status="lost", idle=timedelta(hours=1), has_been_lost=True),
        (_fact("feed.knowledge.accepted"),),
        now=NOW,
    )
    assert knowledge.status == "home"
    assert food_recall_returns_home("lost", food_success=False) is False
    recalled = settle(
        _snap(status="lost", idle=timedelta(hours=1), has_been_lost=True),
        (_fact("recall.completed", food_recall_success=True),),
        now=NOW,
    )
    assert recalled.status == "home"
    assert recalled.has_been_lost is True


def test_nine_hour_decay_mid_nocturnal_then_replay_stable() -> None:
    snap = _snap(last_state_settled_at=NOW - timedelta(hours=9), nocturnal=50)
    first = settle(snap, now=NOW)
    assert first.hunger == 74
    assert first.energy == 76
    replay = settle(
        _snap(hunger=first.hunger, energy=first.energy, last_state_settled_at=NOW),
        now=NOW,
    )
    assert (replay.hunger, replay.energy) == (74, 76)


def test_food_raises_life_quota_reject_does_not() -> None:
    fed = settle(
        _snap(last_state_settled_at=NOW),
        (_fact("feed.food.accepted"),),
        now=NOW,
    )
    assert fed.hunger == 100
    assert fed.energy == 90
    rejected = settle(
        _snap(last_state_settled_at=NOW),
        (_fact("feed.food.accepted", accepted=False, valid=False),),
        now=NOW,
    )
    assert (rejected.hunger, rejected.energy) == (80, 80)
    assert apply_event_life(80, 80, "feed.food.accepted", accepted=False) == (80, 80)


def test_decision_has_no_mood_or_primary_label() -> None:
    decision = settle(_snap(), now=NOW)
    assert isinstance(decision, AwarenessDecision)
    assert not hasattr(decision, "mood")
    assert not hasattr(decision, "primary_label")
    assert "mood" not in decision.__slots__
    assert life_band(80) == "L3"


def test_naive_now_is_rejected() -> None:
    with raises(ValueError, match="now"):
        settle(_snap(), now=datetime(2026, 9, 20, 12, 0))


def test_domain_has_no_io_or_wall_clock() -> None:
    forbidden_roots = {"sqlalchemy", "asyncpg", "httpx", "fastapi"}
    for path in (ROOT / "app" / "domain" / "awareness").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "datetime.now" not in source
        assert "datetime.utcnow" not in source
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".", maxsplit=1)[0] not in forbidden_roots
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.services")
                assert not node.module.startswith("app.repositories")
                assert not node.module.startswith("app.models")


def test_low_nocturnal_covers_one_night_window() -> None:
    start = datetime(2026, 9, 19, 14, 0, tzinfo=UTC)
    end = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
    hunger, energy = decay_life(
        hunger=80,
        energy=80,
        now=end,
        last_state_settled_at=start,
        nocturnal=30,
        timezone="Asia/Shanghai",
    )
    assert hunger == 74
    assert energy == 73
