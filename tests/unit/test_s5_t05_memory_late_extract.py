"""S5-T05: a late extract cannot revive memories or roll the snapshot back."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.domain.memory.late_extract import (
    captured_snapshot_version,
    control_keeps_row,
    extract_write_summaries,
    late_extract_decision,
)
from app.services.memory.window_service import extract_dedupe_key

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "app" / "services" / "memory" / "extract_worker.py"


def test_stale_snapshot_discards_every_candidate() -> None:
    assert (
        late_extract_decision(captured_snapshot_version=1, current_snapshot_version=2)
        == "discard"
    )
    assert late_extract_decision(captured_snapshot_version=None, current_snapshot_version=2) == (
        "discard"
    )
    assert (
        late_extract_decision(captured_snapshot_version=2, current_snapshot_version=2) == "commit"
    )
    assert extract_write_summaries(
        decision="discard",
        summaries=("叫我阿年", "迟到的新事实"),
        controlled_summaries=frozenset(),
    ) == ()


def test_matching_snapshot_still_blocks_sealed_or_deleted_summary() -> None:
    assert control_keeps_row(lifecycle="deleted") is True
    assert control_keeps_row(lifecycle="sealed") is True
    assert control_keeps_row(lifecycle="active") is False
    kept = extract_write_summaries(
        decision="commit",
        summaries=("叫我阿年", "迟到的新事实"),
        controlled_summaries=frozenset({"叫我阿年"}),
    )
    assert kept == ("迟到的新事实",)


def test_task_key_is_one_window_and_payload_keeps_snapshot() -> None:
    window_id = uuid.uuid4()
    key = extract_dedupe_key(window_id)
    assert key == f"extract:window:{window_id}"
    assert "snapshot" not in key
    assert captured_snapshot_version({"snapshot_version": 3}) == 3
    assert captured_snapshot_version({"snapshot_version": "4"}) == 4
    assert captured_snapshot_version({}) is None
    assert captured_snapshot_version({"snapshot_version": 0}) is None


def test_worker_does_not_overwrite_snapshot_or_memory_status() -> None:
    source = WORKER.read_text(encoding="utf-8")
    assert "late_extract_decision" in source
    assert "UPDATE public.spirits" not in source
    assert "UPDATE public.memories" not in source
    assert "bump_snapshot" not in source
    assert "settle_extract" not in source
