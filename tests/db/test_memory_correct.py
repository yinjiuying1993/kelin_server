from __future__ import annotations

import asyncio
import uuid

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.repositories import memory_recall as recall_repo
from app.schemas.memory import MemoryClearRequest, MemoryPatchRequest
from app.services.memory import clear_owned_memories, patch_owned_memory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

INVITE = "CRCT2345"


def test_correction_replaces_address_and_clear_keeps_traits() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_correction(url))


async def _assert_correction(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    old_id = uuid.uuid4()
    edited_id = uuid.uuid4()
    tea_id = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": owner})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, bond, stage"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, "
                "65, 55, 35, 40, 40, 20, 'whelp')"
            ),
            {
                "id": spirit_id,
                "user_id": owner,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, topic_key"
                ") VALUES ("
                ":id, :spirit_id, 'preference', '以后叫我小满', 90, 0.96, "
                "'preference:user:address_name:preferred')"
            ),
            {"id": old_id, "spirit_id": spirit_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence"
                ") VALUES (:id, :spirit_id, 'preference', '旧称呼', 60, 0.96)"
            ),
            {"id": edited_id, "spirit_id": spirit_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, topic_key"
                ") VALUES ("
                ":id, :spirit_id, 'knowledge', '茶的做法', 55, 0.96, "
                "'knowledge:tea:method:general')"
            ),
            {"id": tea_id, "spirit_id": spirit_id},
        )
    await engine.dispose()

    user = CurrentUser(id=owner)
    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    async with claimed_transaction(factory, user) as session:
        try:
            await patch_owned_memory(
                session,
                user,
                tea_id,
                MemoryPatchRequest.model_validate(
                    {
                        "client_id": str(uuid.uuid4()),
                        "expected_version": 1,
                        "action": "correct",
                        "summary": "身份证110101199003078888",
                    }
                ),
            )
        except ApiError as exc:
            assert exc.code == "INVALID_INPUT"
        else:
            raise AssertionError("private summary must be rejected")

    async with claimed_transaction(factory, user) as session:
        corrected = await patch_owned_memory(
            session,
            user,
            edited_id,
            MemoryPatchRequest.model_validate(
                {
                    "client_id": str(uuid.uuid4()),
                    "expected_version": 1,
                    "action": "correct",
                    "summary": "请叫我阿年",
                }
            ),
        )
        recalled = await recall_repo.fetch_prompt_memories(
            session, owner_id=owner, spirit_id=spirit_id
        )
    assert corrected.memory is not None
    assert corrected.memory.summary == "请叫我阿年"
    assert corrected.memory.status == "active"
    assert corrected.memory.version == 2
    assert old_id not in {item.id for item in recalled}
    assert edited_id in {item.id for item in recalled}

    traits, rows = await _stored(url, spirit_id)
    assert traits == (65, 55, 35, 40, 40, 20, "whelp")
    assert rows[old_id] == ("superseded", "以后叫我小满")
    assert rows[edited_id][0] == "active"
    assert rows[tea_id] == ("active", "茶的做法")

    async with claimed_transaction(factory, user) as session:
        await clear_owned_memories(
            session,
            user,
            MemoryClearRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "confirm": "CLEAR_ALL_MEMORIES"}
            ),
        )
    traits, rows = await _stored(url, spirit_id)
    assert traits == (65, 55, 35, 40, 40, 20, "whelp")
    assert rows[edited_id][0] == "deleted"
    assert rows[tea_id][0] == "deleted"
    await runtime.dispose()


async def _stored(
    url: str, spirit_id: uuid.UUID
) -> tuple[tuple[int, int, int, int, int, int, str], dict[uuid.UUID, tuple[str, str]]]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        spirit = (
            await conn.execute(
                text(
                    "SELECT closeness, curiosity, sharpness, nocturnal, stubborn, bond, stage "
                    "FROM public.spirits WHERE id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
        memories = (
            await conn.execute(
                text(
                    "SELECT id, status, summary FROM public.memories WHERE spirit_id = :id"
                ),
                {"id": spirit_id},
            )
        ).all()
    await engine.dispose()
    return (
        (
            int(spirit.closeness),
            int(spirit.curiosity),
            int(spirit.sharpness),
            int(spirit.nocturnal),
            int(spirit.stubborn),
            int(spirit.bond),
            str(spirit.stage),
        ),
        {row.id: (str(row.status), str(row.summary)) for row in memories},
    )
