"""Authoritative snapshot merge. Spec §5.5.

Clients store one aggregate version per spirit. They may replace from bootstrap
or merge a mutation patch; they must not recompute status, stage, quota, or
scholar marks. A patch whose snapshot_version is lower than the stored version
is late: reject it and bootstrap.
"""

from __future__ import annotations

from typing import Literal

PatchMergeDecision = Literal["merge", "bootstrap"]


def patch_merge_decision(
    *,
    current_snapshot_version: int,
    patch_snapshot_version: int,
) -> PatchMergeDecision:
    if current_snapshot_version < 0:
        raise ValueError("current snapshot_version must be >= 0")
    if patch_snapshot_version < 1:
        raise ValueError("patch snapshot_version must be >= 1")
    if patch_snapshot_version < current_snapshot_version:
        return "bootstrap"
    return "merge"


def spirit_version_matches_patch(*, spirit_version: int, snapshot_version: int) -> bool:
    return spirit_version == snapshot_version
