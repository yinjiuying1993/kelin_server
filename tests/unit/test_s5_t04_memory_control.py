"""S5-T04: memory control is a thin API over the central pipeline."""

from __future__ import annotations

from pathlib import Path

from app.domain.central.pipeline import MEMORY_CONTROL_PIPELINE

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "app" / "api" / "v1" / "memories.py"
CONTROL = ROOT / "app" / "services" / "central" / "memory_control.py"
MEMORY = ROOT / "app" / "services" / "memory"


def test_api_forwards_mutations_to_central() -> None:
    source = API.read_text(encoding="utf-8")
    assert source.count("execute_memory_control") == 4
    assert "patch_owned_memory" not in source
    assert "delete_owned_memory" not in source
    assert "clear_owned_memories" not in source


def test_control_bumps_inside_memory_pipeline() -> None:
    assert MEMORY_CONTROL_PIPELINE.index("memory_control_apply") < MEMORY_CONTROL_PIPELINE.index(
        "commit_snapshot"
    )
    source = CONTROL.read_text(encoding="utf-8")
    assert "run_memory_control" in source
    assert "commit_snapshot=False" in source
    assert "run_bootstrap" not in source
    for path in MEMORY.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "app.services.bootstrap" not in text
        assert "app.services.growth" not in text
