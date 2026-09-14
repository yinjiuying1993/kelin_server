from __future__ import annotations

from pathlib import Path

ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "extract.py"


def test_extract_router_does_not_contain_settlement_sql_or_vendor() -> None:
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert "INSERT INTO" not in source
    assert "settle_extract" in source
    assert "extract_result_from_settlement" in source
    assert "INTERNAL_ERROR" not in source
    assert "httpx" not in source
    assert "dashscope" not in source.lower()
    assert "integrations.bailian" not in source
    assert "start_message_id" not in source
    assert "end_message_id" not in source
    assert "CHAT_COMPLETIONS_URL" not in source
