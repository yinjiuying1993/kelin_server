from __future__ import annotations

import asyncio
import uuid

from app.domain.invite_code import invite_code_is_valid
from app.services.invite_code import allocate_invite_code
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

_INSERT_SPIRIT = (
    "INSERT INTO public.spirits ("
    "user_id, client_id, egg, invite_code, "
    "closeness, curiosity, sharpness, nocturnal, stubborn"
    ") VALUES ("
    ":user_id, :client_id, 'wild', :invite, 50, 50, 50, 50, 50)"
)


async def _insert_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})


def test_forced_unique_conflict_retries_and_keeps_outer_transaction() -> None:
    url = upgrade_empty_kelin_test_to_head()
    taken = "ABCD2345"
    free = "EFGH6789"
    occupier = uuid.uuid4()
    owner = uuid.uuid4()

    async def _run() -> str:
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            async with session.begin():
                await _insert_user(session, occupier)
                await _insert_user(session, owner)
                await session.execute(
                    text(_INSERT_SPIRIT),
                    {"user_id": occupier, "client_id": uuid.uuid4(), "invite": taken},
                )
                codes = iter([taken, free])

                async def reserve(code: str) -> None:
                    await session.execute(
                        text(_INSERT_SPIRIT),
                        {"user_id": owner, "client_id": uuid.uuid4(), "invite": code},
                    )

                allocated = await allocate_invite_code(
                    session, reserve, generate=lambda: next(codes)
                )
                count = await session.scalar(text("SELECT count(*) FROM public.spirits"))
                owner_code = await session.scalar(
                    text("SELECT invite_code FROM public.spirits WHERE user_id = :id"),
                    {"id": owner},
                )
        await engine.dispose()
        assert int(count or 0) == 2
        assert owner_code == allocated
        return allocated

    assert asyncio.run(_run()) == free


def test_concurrent_allocations_are_unique_and_valid() -> None:
    url = upgrade_empty_kelin_test_to_head()
    workers = 12

    async def _one() -> str:
        engine = create_async_engine(url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        user_id = uuid.uuid4()
        async with factory() as session:
            async with session.begin():
                await _insert_user(session, user_id)

                async def reserve(code: str) -> None:
                    await session.execute(
                        text(_INSERT_SPIRIT),
                        {"user_id": user_id, "client_id": uuid.uuid4(), "invite": code},
                    )

                allocated = await allocate_invite_code(session, reserve)
        await engine.dispose()
        return allocated

    async def _all() -> list[str]:
        return list(await asyncio.gather(*[_one() for _ in range(workers)]))

    codes = asyncio.run(_all())
    assert len(codes) == workers
    assert len(set(codes)) == workers
    assert all(invite_code_is_valid(code) for code in codes)
