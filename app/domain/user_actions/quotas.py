"""Action-quota lookup. Recall does not spend a daily quota."""

from __future__ import annotations

_CAPABILITY: dict[str, str] = {}
_LIMITS: dict[str, int] = {}


def quota_capability(action_type: str) -> str | None:
    return _CAPABILITY.get(action_type)


def quota_limit(action_type: str) -> int | None:
    capability = quota_capability(action_type)
    if capability is None:
        return None
    return _LIMITS[capability]


def quota_rejection(action_type: str, used: int) -> str | None:
    limit = quota_limit(action_type)
    if limit is None:
        return None
    if used >= limit:
        return "QUOTA_EXCEEDED"
    return None
