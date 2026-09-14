from __future__ import annotations

import asyncio
import secrets
import uuid

from app.db.rls_matrix import RLS_MATRIX, policy_blueprints, role_table_privileges
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


async def _query(
    url: str, sql: str, params: dict[str, object] | None = None
) -> list[tuple[object, ...]]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params or {})
        rows = [tuple(row) for row in result.fetchall()]
    await engine.dispose()
    return rows


async def _scalar(url: str, sql: str, params: dict[str, object] | None = None) -> object:
    rows = await _query(url, sql, params)
    return rows[0][0]


async def _rls_flags(url: str) -> dict[str, tuple[bool, bool]]:
    rows = await _query(
        url,
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r'",
    )
    return {str(name): (bool(enabled), bool(forced)) for name, enabled, forced in rows}


async def _table_owners(url: str) -> dict[str, str]:
    rows = await _query(
        url,
        "SELECT c.relname, pg_catalog.pg_get_userbyid(c.relowner) "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'",
    )
    return {str(name): str(owner) for name, owner in rows}


async def _policy_names(url: str) -> set[tuple[str, str]]:
    rows = await _query(
        url,
        "SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public'",
    )
    return {(str(table), str(name)) for table, name in rows}


async def _has_table_priv(url: str, role: str, table: str, priv: str) -> bool:
    value = await _scalar(
        url,
        "SELECT has_table_privilege(:role, :rel, :priv)",
        {"role": role, "rel": f"public.{table}", "priv": priv},
    )
    return bool(value)


async def _granted_privileges(url: str) -> set[tuple[str, str, str]]:
    granted: set[tuple[str, str, str]] = set()
    roles = ("kelin_api", "kelin_worker", "kelin_scheduler", "kelin_observer")
    privs = ("SELECT", "INSERT", "UPDATE", "DELETE")
    for role in roles:
        for row in RLS_MATRIX:
            for priv in privs:
                if await _has_table_priv(url, role, row.table, priv):
                    granted.add((role, row.table, priv))
    return granted


async def _insert_two_spirits(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO auth.users (id) VALUES (:a), (:b)"),
            {"a": user_a, "b": user_b},
        )
        for user_id in (user_a, user_b):
            invite = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))
            await conn.execute(
                text(
                    "INSERT INTO public.spirits ("
                    "user_id, client_id, egg, invite_code, "
                    "closeness, curiosity, sharpness, nocturnal, stubborn"
                    ") VALUES ("
                    ":user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50)"
                ),
                {"user_id": user_id, "client_id": uuid.uuid4(), "invite": invite},
            )
    await engine.dispose()
    return user_a, user_b


async def _count_as_role(
    url: str,
    role: str,
    sql: str,
    params: dict[str, object] | None = None,
    *,
    claim: str | None = None,
) -> int:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text(f"SET LOCAL ROLE {role}"))
        if claim is not None:
            await conn.execute(
                text("SELECT set_config('request.jwt.claim.sub', :sub, true)"),
                {"sub": claim},
            )
        count = await conn.execute(text(sql), params or {})
        value = int(count.scalar_one())
    await engine.dispose()
    return value


def test_all_business_tables_have_force_rls() -> None:
    url = upgrade_empty_kelin_test_to_head()
    flags = asyncio.run(_rls_flags(url))
    for row in RLS_MATRIX:
        enabled, forced = flags[row.table]
        assert enabled is True, row.table
        assert forced is True, row.table


def test_table_owner_is_migrator_not_api() -> None:
    url = upgrade_empty_kelin_test_to_head()
    owners = asyncio.run(_table_owners(url))
    for row in RLS_MATRIX:
        assert owners[row.table] == "kelin_migrator", row.table
        assert owners[row.table] != "kelin_api"


def test_policies_match_matrix_blueprints() -> None:
    url = upgrade_empty_kelin_test_to_head()
    present = asyncio.run(_policy_names(url))
    expected = {(blueprint.table, blueprint.name) for blueprint in policy_blueprints()}
    assert present == expected
    client_policies = asyncio.run(
        _query(
            url,
            "SELECT policyname FROM pg_policies "
            "WHERE 'anon' = ANY (roles) OR 'authenticated' = ANY (roles)",
        )
    )
    assert client_policies == []


def test_runtime_grants_match_matrix_and_exclude_truncate() -> None:
    url = upgrade_empty_kelin_test_to_head()
    granted = asyncio.run(_granted_privileges(url))
    expected = role_table_privileges() - {("kelin_api", "postcards", "UPDATE")}
    assert granted == expected
    assert ("kelin_observer", "spirits", "SELECT") not in granted
    assert ("kelin_api", "notification_deliveries", "SELECT") not in granted
    can_truncate = asyncio.run(
        _scalar(url, "SELECT has_table_privilege('kelin_api', 'public.spirits', 'TRUNCATE')")
    )
    assert can_truncate is False
    can_update_text = asyncio.run(
        _scalar(
            url,
            "SELECT has_column_privilege('kelin_api', 'public.postcards', 'text', 'UPDATE')",
        )
    )
    can_update_read_at = asyncio.run(
        _scalar(
            url,
            "SELECT has_column_privilege('kelin_api', 'public.postcards', 'read_at', 'UPDATE')",
        )
    )
    assert can_update_text is False
    assert can_update_read_at is True


def test_api_without_claim_cannot_see_rows_and_cannot_read_internal_tables() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_insert_two_spirits(url))
    visible = asyncio.run(_count_as_role(url, "kelin_api", "SELECT count(*) FROM public.spirits"))
    assert visible == 0
    owner_visible = asyncio.run(
        _count_as_role(url, "kelin_migrator", "SELECT count(*) FROM public.spirits")
    )
    assert owner_visible == 0

    async def _internal_denied() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(text("SET LOCAL ROLE kelin_api"))
            try:
                await conn.execute(text("SELECT count(*) FROM public.notification_deliveries"))
            except ProgrammingError as exc:
                assert "permission denied" in str(exc).lower()
            else:
                raise AssertionError("kelin_api must not SELECT notification_deliveries")
        await engine.dispose()

    asyncio.run(_internal_denied())


def test_api_claim_sees_only_owner_spirit() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_a, user_b = asyncio.run(_insert_two_spirits(url))
    seen_a = asyncio.run(
        _count_as_role(
            url,
            "kelin_api",
            "SELECT count(*) FROM public.spirits",
            claim=str(user_a),
        )
    )
    seen_b = asyncio.run(
        _count_as_role(
            url,
            "kelin_api",
            "SELECT count(*) FROM public.spirits WHERE user_id = :other",
            {"other": user_b},
            claim=str(user_a),
        )
    )
    assert seen_a == 1
    assert seen_b == 0
    auth_uid = asyncio.run(
        _count_as_role(
            url,
            "kelin_api",
            "SELECT count(*) FROM public.spirits WHERE user_id = auth.uid()",
            claim=str(user_a),
        )
    )
    assert auth_uid == 1
