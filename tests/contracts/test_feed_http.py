from __future__ import annotations

from pathlib import Path

ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "feeds.py"


def test_feed_router_does_not_contain_settlement_sql() -> None:
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert "INSERT INTO" not in source
    assert "settle_feed" in source
    assert "feed_result_from_settlement" in source
    assert "INTERNAL_ERROR" not in source
    assert "httpx" not in source
    assert "dashscope" not in source.lower()
