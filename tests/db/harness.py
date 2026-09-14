import asyncio

from alembic import command
from app.core.config import get_settings
from app.db.alembic_runner import alembic_config
from app.db.identity import assert_disposable_kelin_test_database
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

HEAD_REVISION = "20260908_0019"
IDENTITY_REVISION = "20260908_0002"


def require_kelin_test_url() -> str:
    settings = get_settings()
    assert settings.app_env == "test"
    url = settings.sqlalchemy_async_url()
    ident = assert_disposable_kelin_test_database(url, settings.app_env)
    assert ident["database"] == "kelin_test"
    return url


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
