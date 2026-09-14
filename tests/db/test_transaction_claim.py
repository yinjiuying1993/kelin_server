from __future__ import annotations

import asyncio
import secrets
import uuid

from app.db.current_user import CurrentUser
from app.db.session import (
    claimed_transaction,
    create_runtime_engine,
    create_session_factory,
    inject_transaction_claim,
    read_claim_role,
    read_claim_sub,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


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


async def _spirit_count(session: AsyncSession) -> int:
    result = await session.scalar(text("SELECT count(*) FROM public.spirits"))
    return int(result or 0)


def test_commit_and_rollback_clear_transaction_claim() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_a, _user_b = asyncio.run(_insert_two_spirits(url))
    asyncio.run(_assert_commit_and_rollback_clear(url, user_a))


async def _assert_commit_and_rollback_clear(url: str, user_a: uuid.UUID) -> None:
    engine = create_async_engine(url)
    user = CurrentUser(id=user_a)
    async with engine.connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL ROLE kelin_api"))
            await inject_transaction_claim(conn, user)
            assert await read_claim_sub(conn) == str(user_a)
            assert await read_claim_role(conn) == "authenticated"
            assert await conn.scalar(text("SELECT current_user")) == "kelin_api"
            assert await conn.scalar(text("SELECT count(*) FROM public.spirits")) == 1

        async with conn.begin():
            assert await read_claim_sub(conn) is None
            assert await read_claim_role(conn) is None
            assert await conn.scalar(text("SELECT current_user")) != "kelin_api"
            await conn.execute(text("SET LOCAL ROLE kelin_api"))
            assert await read_claim_sub(conn) is None
            assert await conn.scalar(text("SELECT count(*) FROM public.spirits")) == 0

        trans = await conn.begin()
        try:
            await conn.execute(text("SET LOCAL ROLE kelin_api"))
            await inject_transaction_claim(conn, user)
            assert await conn.scalar(text("SELECT count(*) FROM public.spirits")) == 1
        finally:
            await trans.rollback()

        async with conn.begin():
            assert await read_claim_sub(conn) is None
            assert await read_claim_role(conn) is None
            await conn.execute(text("SET LOCAL ROLE kelin_api"))
            assert await conn.scalar(text("SELECT count(*) FROM public.spirits")) == 0
    await engine.dispose()


def test_pool_reuse_a_then_b_then_none() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_a, user_b = asyncio.run(_insert_two_spirits(url))
    asyncio.run(_assert_pool_reuse(url, user_a, user_b))


async def _assert_pool_reuse(url: str, user_a: uuid.UUID, user_b: uuid.UUID) -> None:
    engine = create_runtime_engine(url, pool_size=1, max_overflow=0)
    factory = create_session_factory(engine)
    pids: list[int] = []

    async with claimed_transaction(factory, CurrentUser(id=user_a)) as session:
        pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        assert await _spirit_count(session) == 1
        owner = await session.scalar(text("SELECT user_id FROM public.spirits"))
        assert owner == user_a
        assert await read_claim_sub(session) == str(user_a)

    async with claimed_transaction(factory, CurrentUser(id=user_b)) as session:
        pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        assert await _spirit_count(session) == 1
        owner = await session.scalar(text("SELECT user_id FROM public.spirits"))
        assert owner == user_b
        assert await read_claim_sub(session) == str(user_b)

    async with claimed_transaction(factory, None) as session:
        pids.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
        assert await read_claim_sub(session) is None
        assert await _spirit_count(session) == 0

    async with engine.connect() as conn:
        assert await read_claim_sub(conn) is None
        assert await read_claim_role(conn) is None

    assert len(set(pids)) == 1
    await engine.dispose()


def test_claimed_transaction_rollback_does_not_leak_to_next_checkout() -> None:
    url = upgrade_empty_kelin_test_to_head()
    user_a, _user_b = asyncio.run(_insert_two_spirits(url))
    asyncio.run(_assert_helper_rollback(url, user_a))


async def _assert_helper_rollback(url: str, user_a: uuid.UUID) -> None:
    engine = create_runtime_engine(url, pool_size=1, max_overflow=0)
    factory = create_session_factory(engine)
    try:
        async with claimed_transaction(factory, CurrentUser(id=user_a)) as session:
            assert await _spirit_count(session) == 1
            raise RuntimeError("force rollback")
    except RuntimeError:
        pass

    async with claimed_transaction(factory, None) as session:
        assert await read_claim_sub(session) is None
        assert await _spirit_count(session) == 0
    await engine.dispose()


def test_claimed_transaction_provisions_auth_user() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_auth_user_provisioned(url))


async def _assert_auth_user_provisioned(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    async with claimed_transaction(factory, CurrentUser(id=user_id)):
        pass
    probe = create_async_engine(url)
    async with probe.connect() as conn:
        found = await conn.scalar(
            text("SELECT id FROM auth.users WHERE id = :id"),
            {"id": user_id},
        )
    await probe.dispose()
    await engine.dispose()
    assert found == user_id
