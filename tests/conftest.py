import os
from collections.abc import Iterator

import pytest
from app.core.config import get_settings

os.environ.setdefault("APP_ENV", "test")


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
