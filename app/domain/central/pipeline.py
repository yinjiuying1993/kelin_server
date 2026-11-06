"""Four request pipelines. No universal flow. No IO."""

from __future__ import annotations

from app.domain.central.chat_mode import choose_chat_mode
from app.domain.central.consumers import (
    BOOTSTRAP_CONSUMERS,
    CHAT_POST_PERSIST_CONSUMERS,
    TICK_FORBIDDEN_CONSUMERS,
    consumers_for_event,
)
from app.domain.central.locks import LOCK_ORDER
from app.domain.central.types import (
    REQUEST_KINDS,
    RULES_VERSION,
    ChatMode,
    Consumer,
    IdempotencyOutcome,
    OrchestratorPlan,
    OrchestratorRequest,
    PipelineStep,
)

CHAT_PIPELINE: tuple[PipelineStep, ...] = (
    "idempotency",
    "lock_aggregate",
    "awareness_settle",
    "choose_chat_mode",
    "memory_recall",
    "assemble_chat_input",
    "chat_generate",
    "persist_reply",
    "publish_dialogue_completed",
    "memory_window",
    "consume_accepted_facts",
    "project_room",
    "commit_snapshot",
)
USER_ACTION_PIPELINE: tuple[PipelineStep, ...] = (
    "idempotency",
    "lock_aggregate",
    "awareness_settle",
    "user_action_validate",
    "persist_fact_and_quota",
    "awareness_apply_side_effect",
    "memory_ingest",
    "project_room",
    "commit_snapshot",
)
USER_ACTION_REJECTED_PIPELINE: tuple[PipelineStep, ...] = (
    "idempotency",
    "lock_aggregate",
    "user_action_validate",
    "persist_reject",
)
MEMORY_CONTROL_PIPELINE: tuple[PipelineStep, ...] = (
    "idempotency",
    "lock_aggregate",
    "memory_control_apply",
    "project_room",
    "commit_snapshot",
)
BOOTSTRAP_PIPELINE: tuple[PipelineStep, ...] = (
    "lock_aggregate",
    "awareness_settle",
    "project_room",
    "commit_snapshot",
)

_KIND_PIPELINES: dict[str, tuple[PipelineStep, ...]] = {
    "chat": CHAT_PIPELINE,
    "user_action": USER_ACTION_PIPELINE,
    "memory_control": MEMORY_CONTROL_PIPELINE,
    "bootstrap": BOOTSTRAP_PIPELINE,
    "clock_tick": BOOTSTRAP_PIPELINE,
}


def classify_idempotency(
    *,
    stored_hash: str | None,
    stored_state: str | None,
    incoming_hash: str,
) -> IdempotencyOutcome:
    if stored_hash is None or stored_state is None:
        return "proceed"
    if stored_state == "in_progress":
        return "in_progress"
    if stored_hash != incoming_hash:
        return "conflict"
    return "replay"


def plan(request: OrchestratorRequest) -> OrchestratorPlan:
    if request.request_kind not in REQUEST_KINDS:
        raise ValueError(f"unknown request_kind: {request.request_kind}")
    if request.request_kind == "chat":
        return _chat_plan(request)
    if request.request_kind == "user_action":
        return _user_action_plan(request)
    if request.request_kind == "memory_control":
        return _memory_control_plan(request)
    return _tick_plan(request)


def _base(
    request: OrchestratorRequest,
    *,
    pipeline: tuple[PipelineStep, ...],
    chat_mode: ChatMode | None,
    allowed_consumers: tuple[Consumer, ...],
    projection_required: bool,
    persist_reply_required: bool,
    zero_side_effect: bool,
    reason_code: str,
    publish_dialogue_completed: bool,
    bump_snapshot: bool,
) -> OrchestratorPlan:
    return OrchestratorPlan(
        request_kind=request.request_kind,
        pipeline=pipeline,
        chat_mode=chat_mode,
        allowed_consumers=allowed_consumers,
        projection_required=projection_required,
        persist_reply_required=persist_reply_required,
        zero_side_effect=zero_side_effect,
        reason_code=reason_code,
        rules_version=RULES_VERSION,
        lock_order=LOCK_ORDER,
        publish_dialogue_completed=publish_dialogue_completed,
        bump_snapshot=bump_snapshot,
    )


def _chat_plan(request: OrchestratorRequest) -> OrchestratorPlan:
    mode = choose_chat_mode(request)
    consumers: tuple[Consumer, ...] = CHAT_POST_PERSIST_CONSUMERS
    pipeline = CHAT_PIPELINE
    if request.memory_cleared:
        consumers = tuple(item for item in consumers if item != "memory")
        pipeline = tuple(step for step in pipeline if step != "memory_recall")
    persist_index = pipeline.index("persist_reply")
    publish_index = pipeline.index("publish_dialogue_completed")
    if persist_index >= publish_index:
        raise RuntimeError("dialogue.completed must follow persist_reply")
    return _base(
        request,
        pipeline=pipeline,
        chat_mode=mode,
        allowed_consumers=consumers,
        projection_required=True,
        persist_reply_required=True,
        zero_side_effect=False,
        reason_code="PLAN_CHAT",
        publish_dialogue_completed=True,
        bump_snapshot=True,
    )


def _user_action_plan(request: OrchestratorRequest) -> OrchestratorPlan:
    if not request.action_accepted:
        return _base(
            request,
            pipeline=USER_ACTION_REJECTED_PIPELINE,
            chat_mode=None,
            allowed_consumers=(),
            projection_required=False,
            persist_reply_required=False,
            zero_side_effect=True,
            reason_code="PLAN_USER_ACTION_REJECTED",
            publish_dialogue_completed=False,
            bump_snapshot=False,
        )
    event_type = request.action_event_type
    if event_type is None:
        raise ValueError("user_action requires action_event_type")
    return _base(
        request,
        pipeline=USER_ACTION_PIPELINE,
        chat_mode=None,
        allowed_consumers=consumers_for_event(event_type),
        projection_required=True,
        persist_reply_required=False,
        zero_side_effect=False,
        reason_code="PLAN_USER_ACTION",
        publish_dialogue_completed=False,
        bump_snapshot=True,
    )


def _memory_control_plan(request: OrchestratorRequest) -> OrchestratorPlan:
    event_type = request.action_event_type or "memory.corrected"
    return _base(
        request,
        pipeline=MEMORY_CONTROL_PIPELINE,
        chat_mode=None,
        allowed_consumers=consumers_for_event(event_type),
        projection_required=True,
        persist_reply_required=False,
        zero_side_effect=False,
        reason_code="PLAN_MEMORY_CONTROL",
        publish_dialogue_completed=False,
        bump_snapshot=True,
    )


def _tick_plan(request: OrchestratorRequest) -> OrchestratorPlan:
    consumers = BOOTSTRAP_CONSUMERS
    if TICK_FORBIDDEN_CONSUMERS & set(consumers):
        raise RuntimeError("clock/bootstrap must not ingest memory or growth")
    reason = "PLAN_CLOCK_TICK" if request.request_kind == "clock_tick" else "PLAN_BOOTSTRAP"
    return _base(
        request,
        pipeline=BOOTSTRAP_PIPELINE,
        chat_mode=None,
        allowed_consumers=consumers,
        projection_required=True,
        persist_reply_required=False,
        zero_side_effect=False,
        reason_code=reason,
        publish_dialogue_completed=False,
        bump_snapshot=True,
    )


def pipeline_for_kind(kind: str) -> tuple[PipelineStep, ...]:
    try:
        return _KIND_PIPELINES[kind]
    except KeyError as exc:
        raise ValueError(f"unknown request_kind: {kind}") from exc
