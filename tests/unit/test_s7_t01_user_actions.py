"""S7-T01: user actions become facts. Failure does not move bond, mood, or hunger."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

from app.domain.central.consumers import EVENT_CONSUMERS, consumers_for_event
from app.domain.user_actions import EVENT_BY_ACTION, decide_action
from app.domain.user_actions.types import ActionDecision, UserAction

ROOT = Path(__file__).resolve().parents[2] / "app"
EVENT = uuid.uuid4()


def _action(**overrides: object) -> UserAction:
    values: dict[str, object] = {
        "action_type": "food",
        "event_id": EVENT,
        "payload": {},
        "quota_used": 0,
        "status": "home",
        "active_promise": False,
        "knowledge_moderated": False,
        "pact_accepted": False,
    }
    values.update(overrides)
    return UserAction(**values)  # type: ignore[arg-type]


def test_feed_actions_are_rejected() -> None:
    for kind in ("food", "knowledge", "emotion"):
        decision = decide_action(_action(action_type=kind, quota_used=0))
        assert decision.accepted is False
        assert decision.rejection_code == "INVALID_INPUT"
        assert decision.quota_cost == 0
        assert decision.returns_home is False
    assert not hasattr(decision, "hunger")
    assert not hasattr(decision, "bond")


def test_client_deltas_do_not_accept() -> None:
    client = decide_action(
        _action(
            action_type="recall",
            status="lost",
            payload={"method": "food", "bond_delta": 4, "stage": "awake", "mood": 80},
        )
    )
    assert client.accepted is False
    assert client.rejection_code == "INVALID_INPUT"


def test_active_promise_blocks_a_second_create() -> None:
    decision = decide_action(
        _action(
            action_type="promise_create",
            active_promise=True,
            payload={"text": "喝水", "remind_at": "2026-09-20T18:00:00Z"},
        )
    )
    assert decision.rejection_code == "PROMISE_ACTIVE"


def test_home_and_sight_recall_are_rejected() -> None:
    home = decide_action(_action(action_type="recall", status="home", payload={"method": "food"}))
    assert home.rejection_code == "NOT_AWAY"
    away = decide_action(_action(action_type="recall", status="away", payload={"method": "food"}))
    assert away.accepted is True
    assert away.returns_home is True
    sight = decide_action(
        _action(action_type="recall", status="away", payload={"method": "sight_memory"})
    )
    assert sight.rejection_code == "RECALL_METHOD_FORBIDDEN"
    lost_food = decide_action(_action(action_type="food", status="lost"))
    assert lost_food.accepted is False
    assert lost_food.rejection_code == "INVALID_INPUT"
    assert lost_food.returns_home is False


def test_cancel_has_no_positive_growth_signal() -> None:
    decision = decide_action(_action(action_type="promise_cancel", active_promise=True))
    assert decision.accepted is True
    assert decision.event_type == "promise.cancelled"
    assert decision.valid_interaction is False
    assert decision.quota_cost == 0
    assert decision.returns_home is False


def test_replay_returns_the_first_decision_without_quota() -> None:
    first = decide_action(
        _action(action_type="recall", status="away", payload={"method": "food"})
    )
    replay = decide_action(
        _action(
            action_type="recall",
            status="lost",
            payload={"method": "food"},
            quota_used=3,
            settled_event_ids=frozenset({EVENT}),
            prior=first,
        )
    )
    assert replay.accepted is True
    assert replay.replayed is True
    assert replay.quota_cost == 0
    assert replay.event_type == "recall.completed"


def test_feed_events_are_not_user_actions() -> None:
    assert "food" not in EVENT_BY_ACTION
    assert "knowledge" not in EVENT_BY_ACTION
    assert "emotion" not in EVENT_BY_ACTION
    assert "feed.food.accepted" not in EVENT_CONSUMERS
    assert EVENT_BY_ACTION["recall"] == "recall.completed"
    consumers_for_event("recall.completed")


def test_user_actions_do_not_write_bond_mood_or_memory() -> None:
    roots = (ROOT / "domain" / "user_actions", ROOT / "services" / "user_actions")
    banned = (
        "app.services.growth",
        "app.services.emotion",
        "app.services.memory",
        "app.repositories.memory",
        "public.memories",
        "spirit_growth",
        "spirit_emotion",
        "UPDATE public.spirits",
    )
    for folder in roots:
        for path in folder.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            assert "app.services.growth" not in imported
            for token in banned:
                assert token not in source, f"{path}: {token}"
    sample = ActionDecision(
        accepted=True,
        event_type="feed.food.accepted",
        quota_cost=1,
        rejection_code=None,
        valid_interaction=True,
        returns_home=False,
    )
    assert "mood" not in sample.__slots__
