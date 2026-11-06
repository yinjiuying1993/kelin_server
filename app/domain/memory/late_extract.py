"""Late extract loses to a newer snapshot and cannot revive sealed/deleted rows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from app.domain.central.priority import MemoryLifecycle, memory_may_resurrect

LateExtractDecision = Literal["commit", "discard"]


def captured_snapshot_version(payload: Mapping[str, object]) -> int | None:
    raw = payload.get("snapshot_version")
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isdecimal():
        value = int(raw)
    else:
        return None
    if value < 1:
        return None
    return value


def late_extract_decision(
    *,
    captured_snapshot_version: int | None,
    current_snapshot_version: int,
) -> LateExtractDecision:
    if current_snapshot_version < 1:
        raise ValueError("current snapshot_version must be >= 1")
    if captured_snapshot_version is None:
        return "discard"
    if captured_snapshot_version < 1:
        raise ValueError("captured snapshot_version must be >= 1")
    if captured_snapshot_version < current_snapshot_version:
        return "discard"
    return "commit"


def control_keeps_row(*, lifecycle: MemoryLifecycle) -> bool:
    return not memory_may_resurrect(lifecycle=lifecycle)


def extract_write_summaries(
    *,
    decision: LateExtractDecision,
    summaries: tuple[str, ...],
    controlled_summaries: frozenset[str],
) -> tuple[str, ...]:
    if decision == "discard":
        return ()
    kept: list[str] = []
    for summary in summaries:
        text = summary.strip()
        if not text or text in controlled_summaries:
            continue
        kept.append(text)
    return tuple(kept)
