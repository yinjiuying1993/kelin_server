from __future__ import annotations

import pytest
from app.domain.snapshot import patch_merge_decision, spirit_version_matches_patch


def test_equal_or_newer_patch_merges_late_patch_bootstraps() -> None:
    assert patch_merge_decision(current_snapshot_version=0, patch_snapshot_version=1) == "merge"
    assert patch_merge_decision(current_snapshot_version=5, patch_snapshot_version=5) == "merge"
    assert patch_merge_decision(current_snapshot_version=5, patch_snapshot_version=6) == "merge"
    assert patch_merge_decision(current_snapshot_version=5, patch_snapshot_version=4) == "bootstrap"


def test_spirit_version_must_match_patch_snapshot() -> None:
    assert spirit_version_matches_patch(spirit_version=4, snapshot_version=4) is True
    assert spirit_version_matches_patch(spirit_version=3, snapshot_version=4) is False


def test_snapshot_versions_reject_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="current"):
        patch_merge_decision(current_snapshot_version=-1, patch_snapshot_version=1)
    with pytest.raises(ValueError, match="patch"):
        patch_merge_decision(current_snapshot_version=0, patch_snapshot_version=0)
