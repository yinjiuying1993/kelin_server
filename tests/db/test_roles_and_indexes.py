from __future__ import annotations

import asyncio

from app.db.roles import MIGRATOR_ROLE, PREPARED_ROLES, RUNTIME_ROLES, SPEC_INDEXES_6_6
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head


async def _role_flags(url: str) -> dict[str, dict[str, bool]]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, "
                "rolcanlogin, rolreplication "
                "FROM pg_roles WHERE rolname = ANY(:names)"
            ),
            {"names": list(PREPARED_ROLES)},
        )
        flags = {
            str(row.rolname): {
                "super": bool(row.rolsuper),
                "bypassrls": bool(row.rolbypassrls),
                "createdb": bool(row.rolcreatedb),
                "createrole": bool(row.rolcreaterole),
                "login": bool(row.rolcanlogin),
                "replication": bool(row.rolreplication),
            }
            for row in rows.mappings()
        }
    await engine.dispose()
    return flags


async def _index_names(url: str) -> set[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        )
        names = {str(row[0]) for row in rows.fetchall()}
    await engine.dispose()
    return names


async def _worker_inherits_api(url: str) -> bool:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT EXISTS ("
                "  SELECT 1 FROM pg_auth_members m "
                "  JOIN pg_roles r ON r.oid = m.roleid "
                "  JOIN pg_roles u ON u.oid = m.member "
                "  WHERE r.rolname = 'kelin_api' AND u.rolname = 'kelin_worker'"
                ")"
            )
        )
        exists = bool(row.scalar_one())
    await engine.dispose()
    return exists


def test_prepared_roles_are_nobypassrls_and_nosuperuser() -> None:
    url = upgrade_empty_kelin_test_to_head()
    flags = asyncio.run(_role_flags(url))
    assert set(flags) == set(PREPARED_ROLES)
    for name in PREPARED_ROLES:
        assert flags[name]["super"] is False, name
        assert flags[name]["bypassrls"] is False, name
        assert flags[name]["createdb"] is False, name
        assert flags[name]["createrole"] is False, name
        assert flags[name]["replication"] is False, name
    for name in RUNTIME_ROLES:
        assert flags[name]["login"] is True, name
    assert flags[MIGRATOR_ROLE]["login"] is False
    assert asyncio.run(_worker_inherits_api(url)) is False


def test_spec_6_6_indexes_are_queryable() -> None:
    url = upgrade_empty_kelin_test_to_head()
    present = asyncio.run(_index_names(url))
    missing = set(SPEC_INDEXES_6_6) - present
    assert missing == set()
