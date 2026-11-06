"""Preconditions. A failed check spends no quota and changes no spirit numbers."""

from __future__ import annotations

from app.domain.user_actions.types import FORBIDDEN_CLIENT_KEYS, UserAction

def precondition_rejection(action: UserAction) -> str | None:
    if FORBIDDEN_CLIENT_KEYS.intersection(action.payload):
        return "INVALID_INPUT"
    kind = action.action_type
    if kind in {"food", "knowledge", "emotion"}:
        return "INVALID_INPUT"
    if kind == "promise_create":
        return _promise_create(action)
    if kind in {"promise_complete", "promise_cancel"}:
        if not action.active_promise:
            return "PROMISE_NOT_ACTIVE"
        return None
    if kind in {"pact_answer", "pact_complete"}:
        if not action.pact_accepted:
            return "INVALID_INPUT"
        return None
    if kind == "recall":
        return _recall(action)
    return "INVALID_INPUT"


def _promise_create(action: UserAction) -> str | None:
    if action.active_promise:
        return "PROMISE_ACTIVE"
    text = action.payload.get("text")
    remind = action.payload.get("remind_at")
    if not isinstance(text, str) or not text.strip():
        return "INVALID_PROMISE"
    if not isinstance(remind, str) or not remind.strip():
        return "INVALID_PROMISE"
    return None


def _recall(action: UserAction) -> str | None:
    method = action.payload.get("method")
    if method != "food":
        return "RECALL_METHOD_FORBIDDEN"
    if action.status != "away":
        return "NOT_AWAY"
    return None
