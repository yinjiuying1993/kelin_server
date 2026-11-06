"""S10-T03: current guide and prompts do not keep the V0 lock or retired room fields."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "app" / "integrations" / "bailian" / "prompts"
GUIDE = ROOT / "docs" / "ios-integration.md"
README = ROOT / "docs" / "README.md"
HANDOFF = ROOT / "docs" / "ios-handoff.md"

_RETIRED = ("style_samples", "room.layers")
_OLD_LOCKS = ("98d1c742", "fed40c13", "d688d428")


def test_prompts_omit_retired_room_and_style_fields() -> None:
    texts = [path.read_text(encoding="utf-8") for path in PROMPTS.rglob("*.txt")]
    assert texts
    joined = "\n".join(texts)
    for token in ("style_samples", "scholar_marks", "layers", "weather"):
        assert token not in joined


def test_current_guide_is_not_the_v0_sha_lock() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    handoff = HANDOFF.read_text(encoding="utf-8")
    assert "兼容 V0" not in guide
    assert "兼容 V0" not in readme
    assert "兼容 V0" not in handoff
    for token in _OLD_LOCKS:
        assert token not in guide
        assert token not in handoff
    assert "不是现行锁" in guide
    assert "不是现行锁" in readme
    for token in _RETIRED:
        assert token not in handoff
    assert "data.room" in guide
    assert "data.spirit" in guide
    for token in ("public_layers", "room_weather", "scholar_marks"):
        assert token in handoff
    assert "不从卡片读" in handoff
    assert "空列表" in handoff
    assert "active_pact" in handoff
    assert "四件套" in guide
