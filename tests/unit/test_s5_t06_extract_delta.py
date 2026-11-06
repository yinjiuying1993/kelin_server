"""S5-T06: extract drops personality deltas and catchphrase samples."""

from __future__ import annotations

from pathlib import Path

from app.domain.extract import discard_extract_side_channels
from app.providers.extract_schema import require_extract_output

ROOT = Path(__file__).resolve().parents[2]
PROMPT = ROOT / "app" / "integrations" / "bailian" / "prompts" / "extract" / "v2.txt"
EXTRACT_SERVICE = ROOT / "app" / "services" / "extract.py"
EXTRACT_REPO = ROOT / "app" / "repositories" / "extract.py"


def test_model_delta_and_style_sample_are_discarded() -> None:
    parsed = require_extract_output(
        {
            "style_samples": [{"kind": "spirit_catchphrase", "text": "嘿嘿"}],
            "memories": [
                {
                    "type": "preference",
                    "summary": "叫我阿年",
                    "normalized_topic_key": "preference:user:name:nian",
                    "source_quote": "叫我阿年",
                    "signal": "explicit",
                    "tags": [],
                    "salience": 80,
                    "confidence": 0.9,
                    "personality_delta": {"dimension": "closeness", "value": 2},
                }
            ],
        }
    )
    assert len(parsed.memories) == 1
    assert parsed.memories[0].summary == "叫我阿年"
    assert parsed.model_dump().get("style_samples") is None
    assert parsed.memories[0].model_dump().get("personality_delta") is None
    assert "salience" not in parsed.memories[0].model_dump()
    assert discard_extract_side_channels({"style_samples": []}) == {}


def test_prompt_does_not_ask_for_delta_or_catchphrase() -> None:
    prompt = PROMPT.read_text(encoding="utf-8")
    assert "personality_delta" not in prompt
    assert "style_samples" not in prompt
    assert "preference, knowledge, emotion, or relationship" in prompt


def test_extract_path_has_no_direct_style_or_trait_write() -> None:
    service = EXTRACT_SERVICE.read_text(encoding="utf-8")
    repo = EXTRACT_REPO.read_text(encoding="utf-8")
    assert "insert_style_sample" not in service
    assert "apply_trait_patch" not in service
    assert "personality_delta" not in service
    assert "insert_style_sample" not in repo
    assert "apply_trait_patch" not in repo
    assert "INSERT INTO public.style_samples" not in repo
