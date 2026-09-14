"""Process-local Debug failure injection. Spec §14.10. Never logs tokens."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from app.domain.debug import DEBUG_FAILURE_CAPABILITIES

FailureMode = Literal["success", "delay", "cancel", "error"]

_STORE: dict[tuple[uuid.UUID, str], DebugFailure] = {}


@dataclass(frozen=True, slots=True)
class DebugFailure:
    capability: str
    mode: FailureMode
    remaining_calls: int
    latency_ms: int | None


def set_failure(
    owner_id: uuid.UUID,
    *,
    capability: str,
    mode: FailureMode,
    remaining_calls: int,
    latency_ms: int | None,
) -> DebugFailure:
    if capability not in DEBUG_FAILURE_CAPABILITIES:
        raise ValueError("debug failure capability is not allowlisted")
    spec = DebugFailure(
        capability=capability,
        mode=mode,
        remaining_calls=remaining_calls,
        latency_ms=latency_ms,
    )
    _STORE[(owner_id, capability)] = spec
    return spec


def get_failure(owner_id: uuid.UUID, capability: str) -> DebugFailure | None:
    return _STORE.get((owner_id, capability))


def clear_failures(owner_id: uuid.UUID | None = None) -> None:
    if owner_id is None:
        _STORE.clear()
        return
    for key in [item for item in _STORE if item[0] == owner_id]:
        _STORE.pop(key, None)
