import os
from collections.abc import Iterator

import pytest
from app.core.config import get_settings

os.environ.setdefault("APP_ENV", "test")
for _name in (
    "BAILIAN_API_KEY",
    "BAILIAN_WORKSPACE_ID",
    "BAILIAN_APP_ID",
    "BAILIAN_CHAT_MODEL",
    "BAILIAN_EXTRACT_MODEL",
    "BAILIAN_ASR_MODEL",
    "BAILIAN_TTS_MODEL",
    "BAILIAN_TTS_VOICE",
    "BAILIAN_VISION_MODEL",
    "BAILIAN_SAFETY_MODEL",
    "BAILIAN_SEARCH_MODEL",
):
    os.environ[_name] = ""
os.environ["BAILIAN_SEARCH_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
