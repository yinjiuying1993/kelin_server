from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.security import CurrentUser
from app.db.session import (
    claimed_read_transaction,
    create_runtime_engine,
    create_session_factory,
)
from app.repositories import messages as messages_repo
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE = "ABCD2345"
REPO = Path(__file__).resolve().parents[2] / "app" / "repositories" / "messages.py"


def test_repository_sql_is_keyset_without_offset() -> None:
    source = REPO.read_text(encoding="utf-8")
    assert "OFFSET" not in source.upper()
    assert "LIMIT :fetch_limit" in source


def test_keyset_omits_system_and_continues_without_duplicates() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_keyset(url))


async def _assert_keyset(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
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
        base = datetime(2026, 2, 1, tzinfo=UTC)
        for index in range(5):
            await conn.execute(
                text(
                    "INSERT INTO public.messages ("
                    "id, spirit_id, client_id, role, content, source, status, created_at"
                    ") VALUES ("
                    ":id, :spirit_id, :client_id, 'user', '你好', 'text', 'accepted', "
                    ":created_at)"
                ),
                {
                    "id": uuid.uuid4(),
                    "spirit_id": spirit_id,
                    "client_id": uuid.uuid4(),
                    "created_at": base + timedelta(seconds=index),
                },
            )
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, client_id, role, content, source, status, created_at"
                ") VALUES ("
                ":id, :spirit_id, NULL, 'system', 'secret', 'text', 'accepted', :created_at)"
            ),
            {
                "id": uuid.uuid4(),
                "spirit_id": spirit_id,
                "created_at": base + timedelta(seconds=9),
            },
        )
    await engine.dispose()

    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    user = CurrentUser(id=owner)
    async with claimed_read_transaction(factory, user) as session:
        snapshot = await messages_repo.fetch_transaction_now(session)
        owned = await messages_repo.fetch_owned_spirit_id(session, owner)
        assert owned == spirit_id
        first = await messages_repo.list_messages_keyset(
            session,
            spirit_id=spirit_id,
            snapshot_at=snapshot,
            fetch_limit=3,
        )
        assert len(first) == 3
        assert all(row.role != "system" for row in first)
        rest = await messages_repo.list_messages_keyset(
            session,
            spirit_id=spirit_id,
            snapshot_at=snapshot,
            fetch_limit=3,
            cursor_time=first[1].created_at,
            cursor_id=first[1].id,
        )
    first_ids = [row.id for row in first[:2]]
    rest_ids = [row.id for row in rest]
    assert not set(first_ids) & set(rest_ids)
    assert len(first_ids) + len(rest_ids) == 5
    await runtime.dispose()
