"""S9-T04: empty disposable database upgrades to head, then live and ready."""

from __future__ import annotations

import asyncio

from alembic.script import ScriptDirectory
from app.core.config import Settings
from app.core.db_probe import AsyncpgDatabaseProbe
from app.db.alembic_runner import alembic_config
from app.db.identity import database_identity
from app.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import HEAD_REVISION, upgrade_empty_kelin_test_to_head


def test_empty_disposable_db_upgrades_to_head_and_health_is_ready() -> None:
    url = upgrade_empty_kelin_test_to_head()
    ident = database_identity(url)
    assert ident["database"] == "kelin_pytest"
    assert ident["username"] == "kelin_test"
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == [HEAD_REVISION]
    assert asyncio.run(_alembic_version(url)) == HEAD_REVISION

    application = create_app(
        Settings(app_env="test", database_url_api=SecretStr(url)),
        db_probe=AsyncpgDatabaseProbe(SecretStr(url)),
    )
    client = TestClient(application)
    live = client.get("/health/live")
    ready = client.get("/health/ready")
    assert live.status_code == 200
    assert live.json() == {"status": "live"}
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "checks": {"config": "ok", "database": "ok", "migration": "not_applicable"},
    }


async def _alembic_version(url: str) -> str:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        current = await conn.scalar(text("SELECT version_num FROM alembic_version"))
    await engine.dispose()
    return str(current)
