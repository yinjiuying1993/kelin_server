"""S2-T04: wrong request_kind cannot enter another pipeline."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.domain.central import OrchestratorRequest
from app.domain.central.pipeline import (
    BOOTSTRAP_PIPELINE,
    CHAT_PIPELINE,
    MEMORY_CONTROL_PIPELINE,
    USER_ACTION_PIPELINE,
)
from app.services.central.pipelines import (
    PipelineKindMismatch,
    bind_pipeline,
    run_bootstrap,
    run_chat,
    run_memory_control,
    run_user_action,
)
from pytest import raises

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
SPIRIT = uuid.UUID("11111111-1111-1111-1111-111111111111")
EVENT = uuid.UUID("22222222-2222-2222-2222-222222222222")
USER = CurrentUser(id=uuid.UUID("33333333-3333-3333-3333-333333333333"))

_ENTRIES = {
    "bootstrap": run_bootstrap,
    "chat": run_chat,
    "user_action": run_user_action,
    "memory_control": run_memory_control,
}


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


def test_each_entry_binds_its_own_pipeline() -> None:
    bootstrap = bind_pipeline(_request("bootstrap"), entry="bootstrap")
    tick = bind_pipeline(_request("clock_tick"), entry="bootstrap")
    chat = bind_pipeline(_request("chat"), entry="chat")
    action = bind_pipeline(
        _request("user_action", action_event_type="recall.completed"),
        entry="user_action",
    )
    memory = bind_pipeline(
        _request("memory_control", action_event_type="memory.sealed"),
        entry="memory_control",
    )
    assert bootstrap.pipeline == BOOTSTRAP_PIPELINE == tick.pipeline
    assert chat.pipeline == CHAT_PIPELINE
    assert action.pipeline == USER_ACTION_PIPELINE
    assert memory.pipeline == MEMORY_CONTROL_PIPELINE
    assert len({bootstrap.pipeline, chat.pipeline, action.pipeline, memory.pipeline}) == 4
    assert "persist_reply" not in bootstrap.pipeline
    assert "memory_ingest" not in bootstrap.pipeline
    assert "choose_chat_mode" not in action.pipeline
    assert "persist_fact_and_quota" not in memory.pipeline
    assert "memory_control_apply" not in chat.pipeline


def test_wrong_kind_cannot_bind_another_entry() -> None:
    cases = (
        ("chat", "user_action"),
        ("chat", "bootstrap"),
        ("chat", "memory_control"),
        ("user_action", "chat"),
        ("user_action", "bootstrap"),
        ("user_action", "memory_control"),
        ("memory_control", "chat"),
        ("memory_control", "user_action"),
        ("bootstrap", "chat"),
        ("bootstrap", "user_action"),
        ("bootstrap", "memory_control"),
        ("clock_tick", "chat"),
    )
    for actual, entry in cases:
        extras: dict[str, object] = {}
        if actual == "user_action":
            extras["action_event_type"] = "recall.completed"
        if actual == "memory_control":
            extras["action_event_type"] = "memory.deleted"
        with raises(PipelineKindMismatch) as exc:
            bind_pipeline(_request(actual, **extras), entry=entry)
        assert exc.value.entry == entry
        assert exc.value.actual == actual


def test_entry_functions_reject_wrong_kind_before_session() -> None:
    request = _request("user_action", action_event_type="recall.completed")
    with raises(PipelineKindMismatch) as exc:
        asyncio.run(run_chat(None, USER, request))  # type: ignore[arg-type]
    assert exc.value.entry == "chat"
    assert exc.value.actual == "user_action"

    chat_request = _request("chat")
    with raises(PipelineKindMismatch) as exc:
        asyncio.run(run_user_action(None, USER, chat_request))  # type: ignore[arg-type]
    assert exc.value.entry == "user_action"

    with raises(PipelineKindMismatch) as exc:
        asyncio.run(run_memory_control(None, USER, chat_request))  # type: ignore[arg-type]
    assert exc.value.entry == "memory_control"

    with raises(PipelineKindMismatch) as exc:
        asyncio.run(run_bootstrap(None, USER, chat_request))  # type: ignore[arg-type]
    assert exc.value.entry == "bootstrap"


def test_four_entries_are_distinct_functions() -> None:
    assert len({id(fn) for fn in _ENTRIES.values()}) == 4
    assert run_chat is not run_user_action
    assert run_bootstrap is not run_memory_control
