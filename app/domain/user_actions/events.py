"""Success event names. Consumer lists stay in the central whitelist."""

from __future__ import annotations

EVENT_BY_ACTION = {
    "promise_create": "promise.created",
    "promise_complete": "promise.completed",
    "promise_cancel": "promise.cancelled",
    "pact_answer": "pact.answer.submitted",
    "pact_complete": "pact.completed",
    "recall": "recall.completed",
}
VALID_INTERACTION = {
    "promise_create": True,
    "promise_complete": True,
    "promise_cancel": False,
    "pact_answer": True,
    "pact_complete": False,
    "recall": True,
}
