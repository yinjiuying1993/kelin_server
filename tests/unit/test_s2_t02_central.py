"""S2-T02 central orchestrator pure functions."""

from __future__ import annotations

import ast
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.central import (
    EVENT_CONSUMERS,
    FORBIDDEN_SNAPSHOT_FIELDS,
    LOCK_ORDER,
    OrchestratorRequest,
    assemble_snapshot,
    classify_idempotency,
    consumers_for_event,
    pipeline_for_kind,
    plan,
    sort_concurrent_events,
    winner_chat_influence,
    winner_emotion_label,
    winner_knowledge_source,
    winner_memory_lifecycle,
    winner_status,
    winner_trait_candidate,
)
from app.domain.central.pipeline import (
    BOOTSTRAP_PIPELINE,
    CHAT_PIPELINE,
    MEMORY_CONTROL_PIPELINE,
    USER_ACTION_PIPELINE,
)
from app.domain.central.priority import memory_may_resurrect

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
SPIRIT = uuid.UUID("11111111-1111-1111-1111-111111111111")
EVENT = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _request(kind: str, **kwargs: object) -> OrchestratorRequest:
    payload = {
        "request_kind": kind,
        "event_id": EVENT,
        "spirit_id": SPIRIT,
        "occurred_at": NOW,
        "client_payload_hash": "a" * 64,
        "current_snapshot_version": 1,
    }
    payload.update(kwargs)
    return OrchestratorRequest(**payload)  # type: ignore[arg-type]


def test_four_request_kinds_use_distinct_pipelines() -> None:
    chat = plan(_request("chat"))
    action = plan(_request("user_action", action_event_type="recall.completed"))
    memory = plan(_request("memory_control", action_event_type="memory.sealed"))
    bootstrap = plan(_request("bootstrap"))
    tick = plan(_request("clock_tick"))

    assert chat.pipeline == CHAT_PIPELINE == pipeline_for_kind("chat")
    assert action.pipeline == USER_ACTION_PIPELINE
    assert memory.pipeline == MEMORY_CONTROL_PIPELINE
    assert bootstrap.pipeline == BOOTSTRAP_PIPELINE == tick.pipeline
    assert len({chat.pipeline, action.pipeline, memory.pipeline, bootstrap.pipeline}) == 4
    assert chat.request_kind == "chat"
    assert action.request_kind == "user_action"
    assert memory.request_kind == "memory_control"
    assert bootstrap.request_kind == "bootstrap"


def test_wrong_kind_cannot_enter_another_pipeline() -> None:
    chat = plan(_request("chat"))
    assert "user_action_validate" not in chat.pipeline
    assert "memory_control_apply" not in chat.pipeline
    action = plan(_request("user_action", action_event_type="recall.completed"))
    assert "choose_chat_mode" not in action.pipeline
    assert "persist_reply" not in action.pipeline
    memory = plan(_request("memory_control", action_event_type="memory.deleted"))
    assert "persist_fact_and_quota" not in memory.pipeline
    bootstrap = plan(_request("bootstrap"))
    assert "persist_reply" not in bootstrap.pipeline
    assert "memory_ingest" not in bootstrap.pipeline


def test_chat_publishes_dialogue_completed_only_after_persist() -> None:
    pipeline = plan(_request("chat")).pipeline
    assert pipeline.index("persist_reply") < pipeline.index("publish_dialogue_completed")
    assert plan(_request("chat")).persist_reply_required is True
    assert plan(_request("chat")).publish_dialogue_completed is True


def test_lost_chat_mode_is_recall_only_or_safety() -> None:
    lost = plan(_request("chat", status="lost"))
    assert lost.chat_mode == "recall_only"
    safety = plan(_request("chat", status="lost", safety_flags=("high_risk",)))
    assert safety.chat_mode == "safety"
    hurt = plan(_request("chat", primary_emotion_label="委屈"))
    assert hurt.chat_mode == "companion"
    hungry = plan(
        _request(
            "chat",
            hunger_band="L0",
            energy_band="L0",
            primary_emotion_label="饿了",
        )
    )
    assert hungry.chat_mode == "companion"


def test_clock_tick_excludes_memory_ingest_and_growth_trait() -> None:
    tick = plan(_request("clock_tick"))
    assert "memory_ingest" not in tick.pipeline
    assert "growth_settle" not in tick.pipeline
    assert "memory" not in tick.allowed_consumers
    assert "growth" not in tick.allowed_consumers
    assert "chat" not in tick.allowed_consumers
    bootstrap = plan(_request("bootstrap"))
    assert bootstrap.allowed_consumers == tick.allowed_consumers
    assert bootstrap.persist_reply_required is False


def test_rejected_user_action_is_zero_side_effect() -> None:
    rejected = plan(
        _request(
            "user_action",
            action_event_type="recall.completed",
            action_accepted=False,
        )
    )
    assert rejected.zero_side_effect is True
    assert rejected.allowed_consumers == ()
    assert "growth_settle" not in rejected.pipeline
    assert "memory_ingest" not in rejected.pipeline
    assert rejected.reason_code == "PLAN_USER_ACTION_REJECTED"


def test_recall_consumers_come_from_central_not_user_actions() -> None:
    recall = plan(_request("user_action", action_event_type="recall.completed"))
    assert recall.allowed_consumers == consumers_for_event("recall.completed")
    assert recall.allowed_consumers == ("awareness", "memory")


def test_consumer_whitelist_is_not_defined_under_user_actions() -> None:
    assert "recall.completed" in EVENT_CONSUMERS
    assert "feed.food.accepted" not in EVENT_CONSUMERS
    module = inspect.getmodule(consumers_for_event)
    assert module is not None
    assert module.__name__ == "app.domain.central.consumers"
    forbidden_roots = (
        ROOT / "app" / "domain" / "user_actions",
        ROOT / "app" / "services" / "user_actions",
    )
    for folder in forbidden_roots:
        if not folder.exists():
            continue
        for path in folder.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        name = getattr(target, "id", None)
                        assert name != "EVENT_CONSUMERS", path


def test_lock_order_is_fixed() -> None:
    chat = plan(_request("chat"))
    action = plan(_request("user_action", action_event_type="recall.completed"))
    assert chat.lock_order == LOCK_ORDER
    assert action.lock_order == LOCK_ORDER
    assert LOCK_ORDER[0] == "spirit"
    assert LOCK_ORDER[1] == "idempotency_record"
    assert LOCK_ORDER[-1] == "projection"


def test_concurrent_events_sort_by_time_then_event_id() -> None:
    later = NOW + timedelta(seconds=1)
    low = uuid.UUID("00000000-0000-0000-0000-000000000001")
    high = uuid.UUID("00000000-0000-0000-0000-000000000002")
    ordered = sort_concurrent_events(((later, low), (NOW, high), (NOW, low)))
    assert ordered == ((NOW, low), (NOW, high), (later, low))


def test_priority_tables() -> None:
    assert winner_status(("home", "lost", "away")) == "lost"
    assert winner_memory_lifecycle(("active", "deleted", "sealed")) == "deleted"
    assert winner_emotion_label(("平静", "不安", "饿了")) == "不安"
    assert winner_emotion_label(("hungry_and_tired", "hurt")) == "hurt"
    assert winner_trait_candidate(("learning", "user_negative_boundary")) == (
        "user_negative_boundary"
    )
    assert winner_knowledge_source(("builtin_bank", "private_knowledge")) == "private_knowledge"
    assert winner_chat_influence(("personality_band", "safety")) == "safety"
    assert memory_may_resurrect(lifecycle="deleted") is False
    assert memory_may_resurrect(lifecycle="sealed") is False
    assert memory_may_resurrect(lifecycle="active") is True


def test_cleared_memory_plan_keeps_growth_consumers_without_recall() -> None:
    cleared = plan(_request("chat", memory_cleared=True))
    assert "growth" not in cleared.allowed_consumers
    assert "memory" not in cleared.allowed_consumers
    assert "memory_recall" not in cleared.pipeline
    assert "growth_context" not in cleared.pipeline


def test_snapshot_assembly_rejects_v0_room_fields() -> None:
    snap = assemble_snapshot(
        snapshot_version=3,
        status="home",
        hunger_band="L2",
        energy_band="L2",
        mood_band="L2",
        primary_emotion_label="平静",
        stage="whelp",
        stage_image_key="whelp_image",
        letter_key=None,
        text_status=None,
    )
    assert snap.snapshot_version == 3
    assert not FORBIDDEN_SNAPSHOT_FIELDS.intersection(snap.__dataclass_fields__)
    try:
        assemble_snapshot(
            snapshot_version=3,
            status="home",
            hunger_band=None,
            energy_band=None,
            mood_band=None,
            primary_emotion_label=None,
            stage="whelp",
            stage_image_key="whelp_image",
            letter_key=None,
            text_status=None,
            extra={"weather": "sunny", "scholar_marks": []},
        )
    except ValueError as exc:
        assert "weather" in str(exc)
        assert "scholar_marks" in str(exc)
    else:
        raise AssertionError("forbidden snapshot fields must be rejected")


def test_plan_is_deterministic() -> None:
    first = plan(_request("chat", status="study"))
    second = plan(_request("chat", status="study"))
    assert first == second
    assert first.chat_mode == "away_note"
    assert first.rules_version == "kelin-v1-rules-1"


def test_idempotency_outcomes() -> None:
    assert classify_idempotency(stored_hash=None, stored_state=None, incoming_hash="aa") == (
        "proceed"
    )
    assert (
        classify_idempotency(stored_hash="aa", stored_state="succeeded", incoming_hash="aa")
        == "replay"
    )
    assert (
        classify_idempotency(stored_hash="aa", stored_state="succeeded", incoming_hash="bb")
        == "conflict"
    )
    assert (
        classify_idempotency(stored_hash="aa", stored_state="in_progress", incoming_hash="aa")
        == "in_progress"
    )


def test_central_domain_imports_have_no_io() -> None:
    forbidden_roots = {"sqlalchemy", "asyncpg", "httpx", "fastapi"}
    forbidden_from = {"app.services", "app.repositories", "app.db.session"}
    for path in (ROOT / "app" / "domain" / "central").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", maxsplit=1)[0]
                    assert root not in forbidden_roots, path
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in forbidden_from, path
                assert not node.module.startswith("app.services"), path
                assert not node.module.startswith("app.repositories"), path
