from __future__ import annotations

import asyncio
import secrets
import uuid

from alembic.script import ScriptDirectory
from app.db.alembic_runner import alembic_config
from app.db.base import Base
from app.db.roles import SPEC_INDEXES_6_6
from app.models import AccountConsent  # noqa: F401
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import (
    HEAD_REVISION,
    IDENTITY_REVISION,
    downgrade_kelin_test_to,
    upgrade_empty_kelin_test_to,
    upgrade_empty_kelin_test_to_head,
    upgrade_kelin_test_to,
)
from tests.db.schema_inventory import inventory_names

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CHAIN = (
    "20260908_0001",
    "20260908_0002",
    "20260908_0003",
    "20260908_0004",
    "20260908_0005",
    "20260908_0006",
    "20260908_0007",
    "20260908_0008",
    "20260908_0009",
    "20260908_0010",
    "20260908_0011",
    "20260908_0012",
    "20260908_0013",
    "20260908_0014",
    "20260908_0015",
    "20260908_0016",
    "20260908_0017",
    "20260908_0018",
    "20260908_0019",
)


async def _alembic_current(url: str) -> list[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        exists = await conn.execute(text("SELECT to_regclass('public.alembic_version')"))
        if exists.scalar() is None:
            await engine.dispose()
            return []
        rows = await conn.execute(text("SELECT version_num FROM alembic_version"))
        versions = [str(row[0]) for row in rows.fetchall()]
    await engine.dispose()
    return versions


async def _public_tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        )
        names = {str(row[0]) for row in rows.fetchall()}
    await engine.dispose()
    return names


async def _db_columns(url: str) -> dict[tuple[str, str], set[str]]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_schema, table_name, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema IN ('public', 'auth')"
            )
        )
        grouped: dict[tuple[str, str], set[str]] = {}
        for schema, table, column in rows.fetchall():
            grouped.setdefault((str(schema), str(table)), set()).add(str(column))
    await engine.dispose()
    return grouped


async def _index_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        )
        names = {str(row[0]) for row in rows.fetchall()}
    await engine.dispose()
    return names


async def _insert_spirit(url: str) -> uuid.UUID:
    spirit_id = uuid.uuid4()
    user_id = uuid.uuid4()
    invite = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":id, :user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": invite,
            },
        )
    await engine.dispose()
    return spirit_id


async def _spirit_exists(url: str, spirit_id: uuid.UUID) -> bool:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT 1 FROM public.spirits WHERE id = :id"),
            {"id": spirit_id},
        )
        exists = row.first() is not None
    await engine.dispose()
    return exists


def _orm_columns() -> dict[tuple[str, str], set[str]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for table in Base.metadata.tables.values():
        schema = table.schema or "public"
        grouped[(schema, table.name)] = {column.name for column in table.columns}
    return grouped


def test_revision_chain_is_linear_with_unique_head() -> None:
    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_heads() == [HEAD_REVISION]
    walked = [rev.revision for rev in script.walk_revisions()]
    assert walked == list(reversed(CHAIN))
    assert walked[-1] == "20260908_0001"
    assert script.get_revision(CHAIN[0]).down_revision is None
    for previous, current in zip(CHAIN[:-1], CHAIN[1:], strict=True):
        assert script.get_revision(current).down_revision == previous


def test_upgrade_from_identity_revision_preserves_rows_and_reaches_head() -> None:
    url = upgrade_empty_kelin_test_to(IDENTITY_REVISION)
    assert asyncio.run(_alembic_current(url)) == [IDENTITY_REVISION]
    tables_before = asyncio.run(_public_tables(url))
    assert "spirits" in tables_before
    assert "pacts" not in tables_before
    spirit_id = asyncio.run(_insert_spirit(url))

    upgrade_kelin_test_to("head")
    assert asyncio.run(_alembic_current(url)) == [HEAD_REVISION]
    assert asyncio.run(_spirit_exists(url, spirit_id)) is True
    tables_after = asyncio.run(_public_tables(url))
    assert inventory_names() <= tables_after
    assert set(SPEC_INDEXES_6_6) <= asyncio.run(_index_names(url))


def test_downgrade_to_base_then_upgrade_repeats_on_disposable_test_db() -> None:
    url = upgrade_empty_kelin_test_to_head()
    assert asyncio.run(_alembic_current(url)) == [HEAD_REVISION]
    downgrade_kelin_test_to("base")
    tables = asyncio.run(_public_tables(url))
    assert "spirits" not in tables
    assert asyncio.run(_alembic_current(url)) == []
    upgrade_kelin_test_to("head")
    assert asyncio.run(_alembic_current(url)) == [HEAD_REVISION]
    tables_again = asyncio.run(_public_tables(url))
    assert inventory_names() <= tables_again
    assert set(SPEC_INDEXES_6_6) <= asyncio.run(_index_names(url))
    downgrade_kelin_test_to("base")
    upgrade_kelin_test_to("head")
    assert asyncio.run(_alembic_current(url)) == [HEAD_REVISION]
    assert inventory_names() <= asyncio.run(_public_tables(url))


def test_orm_metadata_does_not_drift_from_upgraded_database() -> None:
    url = upgrade_empty_kelin_test_to_head()
    db_columns = asyncio.run(_db_columns(url))
    orm_columns = _orm_columns()
    db_tables = {(schema, table) for schema, table in db_columns if table != "alembic_version"}
    assert orm_columns.keys() == db_tables
    for key, columns in orm_columns.items():
        assert columns == db_columns[key], key
    present = {table for schema, table in db_tables if schema == "public"}
    assert inventory_names() <= present
