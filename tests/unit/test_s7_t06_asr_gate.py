"""S7-T06: cancel, short clips, and blank ASR do not become agent text."""

from app.domain.speech import ASR_AGENT_MIN_MS, asr_agent_block


def test_cancel_short_and_empty_block_the_agent() -> None:
    assert ASR_AGENT_MIN_MS == 800
    assert asr_agent_block(cancelled=True, duration_ms=1200, text="你好") == "cancelled"
    assert asr_agent_block(cancelled=False, duration_ms=799, text=None) == "too_short"
    assert asr_agent_block(cancelled=False, duration_ms=800, text="  ") == "empty"
    assert asr_agent_block(cancelled=False, duration_ms=800, text="你好") is None
