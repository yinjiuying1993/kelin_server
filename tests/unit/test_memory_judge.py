"""Model judgment is stored except when the summary already exists."""

from __future__ import annotations

from app.domain.memory.judge import judgment_memories


def test_model_memories_are_kept_without_signal_or_salience_gates() -> None:
    chosen = judgment_memories(
        {
            "memories": [
                {"type": "preference", "summary": "喜欢海贼王"},
                {"type": "knowledge", "summary": "家在河南信阳"},
                {"summary": "会塔罗牌"},
            ]
        },
        set(),
    )
    assert chosen is not None
    assert [item.summary for item in chosen] == ["喜欢海贼王", "家在河南信阳", "会塔罗牌"]
    assert chosen[2].memory_type == "preference"


def test_identical_summary_is_not_stored_again() -> None:
    chosen = judgment_memories(
        {"memories": [{"type": "preference", "summary": " 喜欢海贼王 "}, {"type": "preference", "summary": "喜欢港片"}]},
        {"喜欢海贼王"},
    )
    assert chosen is not None
    assert [item.summary for item in chosen] == ["喜欢港片"]


def test_empty_list_means_nothing_to_store() -> None:
    assert judgment_memories({"memories": []}, set()) == ()


def test_unusable_payload_is_not_a_decision() -> None:
    assert judgment_memories({"reply": "好"}, set()) is None
    assert judgment_memories([], set()) is None
