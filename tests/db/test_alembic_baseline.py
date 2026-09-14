import asyncio

from alembic.script import ScriptDirectory
from app.db.alembic_runner import alembic_config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import HEAD_REVISION, upgrade_empty_kelin_test_to_head


def test_empty_database_upgrades_to_unique_head() -> None:
    url = upgrade_empty_kelin_test_to_head()
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == [HEAD_REVISION]

    async def _assert_upgraded() -> None:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            current = await conn.execute(text("SELECT version_num FROM alembic_version"))
            assert [row[0] for row in current.fetchall()] == [HEAD_REVISION]
            extension = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'pgcrypto'")
            )
            assert extension.scalar_one() == "pgcrypto"
        await engine.dispose()

    asyncio.run(_assert_upgraded())
