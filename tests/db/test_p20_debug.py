"""P20 Debug mutations stay owner-scoped and bump snapshot_version."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.debug.failures import get_failure
from app.schemas.debug import (
    DebugFailureRequest,
    DebugResetRequest,
    DebugSpiritStateRequest,
)
from app.services.debug import apply_spirit_state, inject_failure, reset_account
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


async def _insert_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _insert_spirit(url: str, *, user_id: uuid.UUID, spirit_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, name, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, version"
                ") VALUES ("
                ":id, :user_id, :client_id, '雾生', 'warm', 'SETDB234', "
                "65, 55, 35, 40, 40, 1)"
            ),
            {"id": spirit_id, "user_id": user_id, "client_id": uuid.uuid4()},
        )
        await conn.execute(
            text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
    await engine.dispose()


def test_debug_state_reset_and_failure_injection() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_debug(url))


async def _assert_debug(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _insert_user(url, owner_id)
    await _insert_spirit(url, user_id=owner_id, spirit_id=spirit_id)
    owner = CurrentUser(id=owner_id)
    body = DebugSpiritStateRequest.model_validate({"status": "lost", "hunger": 12})
    async with claimed_transaction(factory, owner) as session:
        settled = await apply_spirit_state(session, owner, body, now=NOW)
    assert settled.spirit.status == "lost"
    assert settled.spirit.hunger == 12
    assert settled.snapshot_version == 2
    inject_failure(
        owner,
        DebugFailureRequest(capability="chat", mode="error", remaining_calls=1, latency_ms=None),
    )
    assert get_failure(owner_id, "chat") is not None
    async with claimed_transaction(factory, owner) as session:
        reset = await reset_account(
            session,
            owner,
            DebugResetRequest(confirm="RESET_MY_DEBUG_ACCOUNT"),
            now=NOW,
        )
    assert reset.spirit.status == "home"
    assert reset.spirit.hunger == 80
    assert reset.snapshot_version == 3
    assert get_failure(owner_id, "chat") is None
    await engine.dispose()
