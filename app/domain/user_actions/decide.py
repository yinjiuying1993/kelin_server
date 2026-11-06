"""Map an accepted action to one fact. Consumers stay on the central whitelist."""

from __future__ import annotations

from dataclasses import replace

from app.domain.user_actions.events import EVENT_BY_ACTION, VALID_INTERACTION
from app.domain.user_actions.preconditions import precondition_rejection
from app.domain.user_actions.quotas import quota_rejection
from app.domain.user_actions.types import ActionDecision, UserAction


def decide_action(action: UserAction) -> ActionDecision:
    if action.prior is not None and action.event_id in action.settled_event_ids:
        return replace(action.prior, quota_cost=0, replayed=True)
    rejected = precondition_rejection(action)
    if rejected is not None:
        return _reject(rejected)
    quota = quota_rejection(action.action_type, action.quota_used)
    if quota is not None:
        return _reject(quota)
    event = EVENT_BY_ACTION[action.action_type]
    return ActionDecision(
        accepted=True,
        event_type=event,
        quota_cost=0,
        rejection_code=None,
        valid_interaction=VALID_INTERACTION[action.action_type],
        returns_home=action.action_type == "recall",
    )


def _reject(code: str) -> ActionDecision:
    return ActionDecision(
        accepted=False,
        event_type=None,
        quota_cost=0,
        rejection_code=code,
        valid_interaction=False,
        returns_home=False,
    )
