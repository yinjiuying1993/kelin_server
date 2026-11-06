"""E6: visit.completed writes satisfied and upserts the room four-piece."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.spirit import CreateSpiritRequest
from app.services.central.visit_completed import publish_visit_completed
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def test_visit_sets_satisfied_and_room_without_rewriting_hunger() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_visit(url))


async def _assert_visit(url: str) -> None:
    owner = uuid.uuid4()
    auth = create_async_engine(url)
    async with auth.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": owner})
    await auth.dispose()
    user = CurrentUser(id=owner)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(
            session,
            user,
            CreateSpiritRequest.model_validate(
                {
                    "client_id": str(uuid.uuid4()),
                    "egg": "warm",
                    "name": "未名",
                    "consents": {
                        "ai_disclosure": {
                            "document_version": "2026-09",
                            "explicitly_accepted": True,
                        },
                        "data_notice": {"document_version": "2026-09", "displayed": True},
                        "user_terms": {"document_version": "2026-09", "displayed": True},
                    },
                }
            ),
        )
    seed = create_async_engine(url)
    async with seed.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.spirit_growth ("
                "spirit_id, closeness, curiosity, sharpness, nocturnal, stubborn, bond"
                ") VALUES (:id, 50, 50, 50, 50, 50, 0) "
                "ON CONFLICT (spirit_id) DO NOTHING"
            ),
            {"id": created.spirit_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_awareness ("
                "spirit_id, status, hunger, energy, last_interact_at, last_state_settled_at"
                ") VALUES (:id, 'home', 77, 66, :now, :now) "
                "ON CONFLICT (spirit_id) DO UPDATE SET "
                "hunger = 77, energy = 66, last_state_settled_at = EXCLUDED.last_state_settled_at"
            ),
            {"id": created.spirit_id, "now": NOW},
        )
    await seed.dispose()
    visit_id = uuid.uuid4()
    wrote = await publish_visit_completed(
        factory,
        owner_id=user.id,
        spirit_id=created.spirit_id,
        visit_id=visit_id,
        now=NOW,
    )
    assert wrote is True
    mood, label, realtime, hunger, energy, room_label, room_image, room_status = await _row(
        url, created.spirit_id
    )
    assert int(mood) == 60
    assert label == "satisfied"
    assert realtime == "satisfied"
    assert int(hunger) == 77
    assert int(energy) == 66
    assert room_label == "满足"
    assert room_image == "whelp_image"
    assert room_status == "home"

    again = await publish_visit_completed(
        factory,
        owner_id=user.id,
        spirit_id=created.spirit_id,
        visit_id=visit_id,
        now=NOW,
    )
    assert again is True
    mood2, label2, realtime2, hunger2, energy2, room_label2, *_rest = await _row(
        url, created.spirit_id
    )
    assert (mood2, label2, realtime2) == (mood, label, realtime)
    assert (hunger2, energy2) == (77, 66)
    assert room_label2 == "满足"
    await engine.dispose()


async def _row(url: str, spirit_id: uuid.UUID) -> tuple[object, ...]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT e.mood, e.primary_label, e.realtime_emotion, a.hunger, a.energy, "
                    "r.primary_emotion_label, r.stage_image_key, r.status "
                    "FROM public.spirit_emotion e "
                    "JOIN public.spirit_awareness a ON a.spirit_id = e.spirit_id "
                    "JOIN public.room_projections r ON r.spirit_id = e.spirit_id "
                    "WHERE e.spirit_id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return tuple(row)
