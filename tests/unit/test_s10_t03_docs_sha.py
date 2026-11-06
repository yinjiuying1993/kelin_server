"""S10-T03: prompts omit retired public fields."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "app" / "integrations" / "bailian" / "prompts"
_RETIRED = ("style_samples", "scholar_marks", "layers")


def test_prompts_omit_retired_public_fields() -> None:
    files = list(PROMPTS.rglob("*.txt"))
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        for token in _RETIRED:
            assert token not in text, path.name
        assert "weather" not in text, path.name
