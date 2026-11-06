"""S5-T02: DB check rejects relation/speech/sight memory types."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE = "T5TYP234"
ALLOWED = ("preference", "knowledge", "emotion", "relationship")
ILLEGAL = ("relation", "speech", "sight")


async def _execute(url: str, sql: str, params: dict[str, object] | None = None) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text(sql), params or {})
    await engine.dispose()


async def _scalar(url: str, sql: str, params: dict[str, object] | None = None) -> object:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = (await conn.execute(text(sql), params or {})).scalar()
    await engine.dispose()
    return value


def test_illegal_memory_types_fail_ck_memories_type() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_types(url))


async def _assert_types(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _execute(url, "INSERT INTO auth.users (id) VALUES (:id)", {"id": user_id})
    await _execute(
        url,
        "INSERT INTO public.spirits ("
        "id, user_id, client_id, egg, invite_code, "
        "closeness, curiosity, sharpness, nocturnal, stubborn"
        ") VALUES ("
        ":id, :user_id, :client_id, 'warm', :invite, 50, 50, 50, 50, 50)",
        {
            "id": spirit_id,
            "user_id": user_id,
            "client_id": uuid.uuid4(),
            "invite": INVITE,
        },
    )
    definition = str(
        await _scalar(
            url,
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_memories_type'",
        )
    )
    for kind in ALLOWED:
        assert kind in definition
    assert "speech" not in definition
    assert "sight" not in definition
    assert "relation" not in definition.replace("relationship", "")

    for kind in ALLOWED:
        await _execute(
            url,
            "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
            "VALUES (:spirit_id, :type, :summary, 80, 0.900)",
            {"spirit_id": spirit_id, "type": kind, "summary": f"{kind} 刻痕"},
        )
    count = await _scalar(
        url,
        "SELECT count(*) FROM public.memories WHERE spirit_id = :spirit_id",
        {"spirit_id": spirit_id},
    )
    assert int(count or 0) == 4

    legal_id = uuid.uuid4()
    await _execute(
        url,
        "INSERT INTO public.memories (id, spirit_id, type, summary, salience, confidence) "
        "VALUES (:id, :spirit_id, 'knowledge', '可更新', 80, 0.900)",
        {"id": legal_id, "spirit_id": spirit_id},
    )
    for kind in ILLEGAL:
        try:
            await _execute(
                url,
                "INSERT INTO public.memories (spirit_id, type, summary, salience, confidence) "
                "VALUES (:spirit_id, :type, :summary, 80, 0.900)",
                {"spirit_id": spirit_id, "type": kind, "summary": f"{kind} 非法"},
            )
        except DBAPIError as exc:
            assert "ck_memories_type" in str(exc.orig)
        else:
            raise AssertionError(f"{kind} memory type must be rejected")
        try:
            await _execute(
                url,
                "UPDATE public.memories SET type = :type WHERE id = :id",
                {"id": legal_id, "type": kind},
            )
        except DBAPIError as exc:
            assert "ck_memories_type" in str(exc.orig)
        else:
            raise AssertionError(f"{kind} memory type update must be rejected")
