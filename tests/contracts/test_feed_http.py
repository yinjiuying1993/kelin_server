from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.main import create_app

ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "feeds.py"


def test_feed_router_is_removed() -> None:
    assert not ROUTER_PATH.is_file()
    paths = set(create_app(Settings(app_env="test")).openapi().get("paths", {}))
    assert "/api/v1/feed" not in paths
    assert "/api/v1/feeds/{feed_id}" not in paths
