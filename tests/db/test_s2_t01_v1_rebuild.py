"""S2-T01: V1 rebuild revision after 20260908_0019."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import HEAD_REVISION, upgrade_empty_kelin_test_to_head

V1_TABLES = (
    "spirit_awareness",
    "chat_overlays",
    "room_projections",
    "action_events",
    "action_quotas",
)


async def _fetch_names(url: str, sql: str, params: dict[str, object] | None = None) -> list[str]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(text(sql), params or {})
        names = [str(row[0]) for row in rows.fetchall()]
    await engine.dispose()
    return names


async def _fetchone(
    url: str, sql: str, params: dict[str, object] | None = None
) -> tuple[object, ...]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (await conn.execute(text(sql), params or {})).one()
    await engine.dispose()
    return tuple(row)


async def _scalar(url: str, sql: str, params: dict[str, object] | None = None) -> object:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = (await conn.execute(text(sql), params or {})).scalar()
    await engine.dispose()
    return value


async def _execute(url: str, sql: str, params: dict[str, object] | None = None) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text(sql), params or {})
    await engine.dispose()


def test_v1_rebuild_head_schema_rls_and_retired_writes() -> None:
    url = upgrade_empty_kelin_test_to_head()
    current = asyncio.run(_scalar(url, "SELECT version_num FROM alembic_version"))
    assert current == HEAD_REVISION == "20261020_0105"

    tables = set(
        asyncio.run(
            _fetch_names(
                url,
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'",
            )
        )
    )
    assert set(V1_TABLES) <= tables
    spirit_cols = set(
        asyncio.run(
            _fetch_names(
                url,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'spirits'",
            )
        )
    )
    assert "snapshot_version" in spirit_cols
    memory_cols = set(
        asyncio.run(
            _fetch_names(
                url,
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'memories'",
            )
        )
    )
    assert {
        "topic_key",
        "source_event_id",
        "rules_version",
        "confirm_count",
        "last_confirmed_at",
        "decision_audit",
    } <= memory_cols

    for table in V1_TABLES:
        relrow, forcerow, owner = asyncio.run(
            _fetchone(
                url,
                "SELECT c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = :table",
                {"table": table},
            )
        )
        assert relrow is True, table
        assert forcerow is True, table
        assert owner == "kelin_migrator", table

    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    asyncio.run(_execute(url, "INSERT INTO auth.users (id) VALUES (:id)", {"id": user_id}))
    asyncio.run(
        _execute(
            url,
            "INSERT INTO public.spirits ("
            "id, user_id, client_id, egg, invite_code, "
            "closeness, curiosity, sharpness, nocturnal, stubborn"
            ") VALUES ("
            ":id, :user_id, :client_id, 'wild', 'ABCDEFGH', 50, 50, 50, 50, 50)",
            {"id": spirit_id, "user_id": user_id, "client_id": uuid.uuid4()},
        )
    )
    asyncio.run(
        _execute(
            url,
            "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
            "VALUES (:spirit_id, 'relationship', '称呼', 90, 0.9)",
            {"spirit_id": spirit_id},
        )
    )
    try:
        asyncio.run(
            _execute(
                url,
                "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
                "VALUES (:spirit_id, 'speech', '口头禅', 90, 0.9)",
                {"spirit_id": spirit_id},
            )
        )
    except DBAPIError as exc:
        assert "ck_memories_type" in str(exc.orig)
    else:
        raise AssertionError("speech memory type must be rejected")

    try:
        asyncio.run(
            _execute(
                url,
                "INSERT INTO public.style_samples (spirit_id, kind, text) "
                "VALUES (:spirit_id, 'user_dialect', '嗯')",
                {"spirit_id": spirit_id},
            )
        )
    except DBAPIError as exc:
        assert "v1 retired write path" in str(exc.orig)
    else:
        raise AssertionError("style_samples insert must be blocked")
