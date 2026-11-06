"""S6-T04: memory control recomputes familiarity and leaves bond, traits, and stage."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.memory import MemoryDeleteRequest
from app.services.central import execute_memory_control
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "T6FAM234"


async def _seed(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    evidence = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    body = json.dumps([{"evidence_ids": [str(item) for item in evidence]}])
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, stage, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, bond"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, 'formed', "
                "70, 55, 40, 40, 40, 40)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_growth ("
                "spirit_id, closeness, curiosity, sharpness, nocturnal, stubborn, "
                "bond, stage, stage_image_key, familiarity_summaries"
                ") VALUES ("
                ":id, 70, 55, 40, 40, 40, 40, 'formed', 'formed_image', "
                "CAST(:summaries AS jsonb))"
            ),
            {"id": spirit_id, "summaries": body},
        )
        types = ("preference", "knowledge", "emotion")
        for memory_id, memory_type in zip(evidence, types, strict=True):
            await conn.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence"
                    ") VALUES (:id, :spirit_id, :type, :summary, 80, 0.900)"
                ),
                {
                    "id": memory_id,
                    "spirit_id": spirit_id,
                    "type": memory_type,
                    "summary": "叫我阿年",
                },
            )
    await engine.dispose()
    return evidence


async def _stored(url: str, spirit_id: uuid.UUID) -> tuple[object, ...]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT g.bond, g.closeness, g.stage, s.bond, s.closeness, s.stage, "
                    "g.familiarity_summaries "
                    "FROM public.spirit_growth g "
                    "JOIN public.spirits s ON s.id = g.spirit_id "
                    "WHERE g.spirit_id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return row[0], row[1], row[2], row[3], row[4], row[5], row[6]


def test_delete_drops_evidence_and_keeps_stage() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_delete(url))


async def _assert_delete(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    dropped, kept_a, kept_b = await _seed(url, user_id, spirit_id)
    user = CurrentUser(id=user_id)
    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    async with claimed_transaction(factory, user) as session:
        await execute_memory_control(
            session,
            user,
            MemoryDeleteRequest.model_validate(
                {"client_id": str(uuid.uuid4()), "expected_version": 1}
            ),
            memory_id=dropped,
            now=NOW,
        )
    bond, closeness, stage, spirit_bond, spirit_close, spirit_stage, raw = await _stored(
        url, spirit_id
    )
    assert (int(bond), int(closeness), str(stage)) == (40, 70, "formed")
    assert (int(spirit_bond), int(spirit_close), str(spirit_stage)) == (40, 70, "formed")
    payload = raw if isinstance(raw, list) else json.loads(str(raw))
    assert payload == [{"evidence_ids": [str(kept_a), str(kept_b)]}]
    encoded = json.dumps(payload)
    assert str(dropped) not in encoded
    assert "叫我阿年" not in encoded
    await runtime.dispose()
