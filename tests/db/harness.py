import asyncio
import os

from alembic import command
from app.core.config import get_settings
from app.db.alembic_runner import alembic_config
from app.db.identity import (
    KELIN_PYTEST_DB_NAME,
    KELIN_TEST_DB_NAME,
    assert_disposable_kelin_test_database,
)
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine

HEAD_REVISION = "20261020_0105"
IDENTITY_REVISION = "20260908_0002"


def disposable_test_url(url: str) -> str:
    """Point schema resets at kelin_pytest. kelin_test stays the API database."""
    parsed = make_url(url)
    if parsed.database == KELIN_TEST_DB_NAME:
        parsed = parsed.set(database=KELIN_PYTEST_DB_NAME)
    return parsed.render_as_string(hide_password=False)


def require_kelin_test_url() -> str:
    settings = get_settings()
    assert settings.app_env == "test"
    url = disposable_test_url(settings.sqlalchemy_async_url())
    os.environ["DATABASE_URL_API"] = url
    ident = assert_disposable_kelin_test_database(url, settings.app_env)
    assert ident["database"] == KELIN_PYTEST_DB_NAME
    asyncio.run(ensure_disposable_database(url))
    return url


async def ensure_disposable_database(url: str) -> None:
    admin = make_url(url).set(database="postgres").render_as_string(hide_password=False)
    engine = create_async_engine(admin, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        found = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": KELIN_PYTEST_DB_NAME},
        )
        if found is None:
            await conn.execute(text(f"CREATE DATABASE {KELIN_PYTEST_DB_NAME} OWNER kelin_test"))
    await engine.dispose()


async def reset_disposable_test_schemas(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS auth CASCADE"))
        await conn.execute(text("DROP SCHEMA IF EXISTS private CASCADE"))
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO kelin_test"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
    await engine.dispose()


def upgrade_kelin_test_to(revision: str) -> str:
    url = require_kelin_test_url()
    command.upgrade(alembic_config(), revision)
    return url


def downgrade_kelin_test_to(revision: str) -> str:
    url = require_kelin_test_url()
    command.downgrade(alembic_config(), revision)
    return url


def upgrade_empty_kelin_test_to(revision: str) -> str:
    url = require_kelin_test_url()
    asyncio.run(reset_disposable_test_schemas(url))
    command.upgrade(alembic_config(), revision)
    return url


def upgrade_empty_kelin_test_to_head() -> str:
    return upgrade_empty_kelin_test_to("head")
