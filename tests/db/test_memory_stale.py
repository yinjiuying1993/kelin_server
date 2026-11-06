from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.repositories import memory_recall as recall_repo
from app.services.memory import load_memory_page
from app.services.memory.stale_worker import process_stale_memories
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 10, 19, tzinfo=UTC)
INVITE = "STLE2345"
SECRET = b"stale-cursor-hmac"


def test_worker_marks_old_low_salience_stale_without_deleting() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_stale(url))


async def _assert_stale(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    old_low = uuid.uuid4()
    old_high = uuid.uuid4()
    recent_low = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": owner})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, 50, 50, 50, 50, 50)"
            ),
            {
                "id": spirit_id,
                "user_id": owner,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
            },
        )
        await _memory(conn, old_low, spirit_id, 40, NOW - timedelta(days=91))
        await _memory(conn, old_high, spirit_id, 80, NOW - timedelta(days=91))
        await _memory(conn, recent_low, spirit_id, 40, NOW - timedelta(days=2))
    await engine.dispose()

    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    marked = await process_stale_memories(factory, now=NOW)
    assert marked == 1

    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        recalled = await recall_repo.fetch_prompt_memories(
            session, owner_id=owner, spirit_id=spirit_id
        )
        page = await load_memory_page(
            session, user, memory_filter="all", cursor=None, limit=20, secret=SECRET
        )
    assert old_low not in {item.id for item in recalled}
    assert {old_high, recent_low} <= {item.id for item in recalled}
    assert old_low not in {item.id for item in page.items}
    assert page.tombstones == []

    stored = await _rows(url, spirit_id)
    assert stored[old_low] == ("stale", "旧的低显著")
    assert stored[old_high][0] == "active"
    assert stored[recent_low][0] == "active"
    assert len(stored) == 3
    await runtime.dispose()


async def _memory(conn, memory_id: uuid.UUID, spirit_id: uuid.UUID, salience: int, confirmed: datetime) -> None:
    await conn.execute(
        text(
            "INSERT INTO public.memories ("
            "id, spirit_id, type, summary, salience, confidence, last_confirmed_at"
            ") VALUES ("
            ":id, :spirit_id, 'preference', :summary, :salience, 0.96, :confirmed)"
        ),
        {
            "id": memory_id,
            "spirit_id": spirit_id,
            "summary": "旧的低显著" if salience < 50 else "仍要记住",
            "salience": salience,
            "confirmed": confirmed,
        },
    )


async def _rows(url: str, spirit_id: uuid.UUID) -> dict[uuid.UUID, tuple[str, str]]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT id, status, summary FROM public.memories WHERE spirit_id = :id"),
                {"id": spirit_id},
            )
        ).all()
    await engine.dispose()
    return {row.id: (str(row.status), str(row.summary)) for row in rows}
