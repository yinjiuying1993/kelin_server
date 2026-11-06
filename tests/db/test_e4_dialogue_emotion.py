"""E4: dialogue.completed clears hurt. Replay does not spend energy twice."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.services.central import publish_dialogue_completed
from app.services.memory.window_service import DialogueCompletedFact
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)


def test_dialogue_clears_hurt_and_replay_keeps_life() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_dialogue(url))


async def _assert_dialogue(url: str) -> None:
    owner = uuid.uuid4()
    spirit_id = uuid.uuid4()
    event_id = uuid.uuid4()
    await _seed(url, owner, spirit_id)
    user = CurrentUser(id=owner)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    fact = DialogueCompletedFact(
        event_id=event_id,
        spirit_id=spirit_id,
        conversation_window_id=uuid.uuid4(),
        occurred_at=NOW,
    )
    async with claimed_transaction(factory, user) as session:
        await publish_dialogue_completed(session, user, fact)
    mood, label, realtime, expires, hunger, energy = await _row(url, spirit_id)
    assert int(mood) == 60
    assert label == "calm"
    assert realtime is None
    assert expires is None
    assert int(hunger) == 80
    assert int(energy) == 79
    image, room_label, letter, text_status, room_status = await _room(url, spirit_id)
    assert image == "whelp_image"
    assert room_label == "平静"
    assert letter is None
    assert text_status == "home"
    assert room_status == "home"

    async with claimed_transaction(factory, user) as session:
        await publish_dialogue_completed(session, user, fact)
    again_mood, again_label, again_realtime, again_expires, hunger, energy = await _row(
        url, spirit_id
    )
    assert int(again_mood) == 60
    assert again_label == "calm"
    assert again_realtime is None
    assert again_expires == expires
    assert int(hunger) == 80
    assert int(energy) == 79
    again_image, again_room_label, again_letter, again_text, again_status = await _room(
        url, spirit_id
    )
    assert again_image == image
    assert again_room_label == room_label
    assert again_letter == letter
    assert again_text == text_status
    assert again_status == room_status
    await engine.dispose()


async def _room(url: str, spirit_id: uuid.UUID) -> tuple[object, ...]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT stage_image_key, primary_emotion_label, letter_key, "
                    "text_status, status FROM public.room_projections WHERE spirit_id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return tuple(row)


async def _seed(url: str, owner: uuid.UUID, spirit_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": owner})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, name, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, "
                "hatched_at, last_interact_at"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', 'E4DLG234', '未名', "
                "50, 50, 50, 50, 50, :now, :now)"
            ),
            {"id": spirit_id, "user_id": owner, "client_id": uuid.uuid4(), "now": NOW},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_awareness ("
                "spirit_id, status, hunger, energy, last_interact_at, last_state_settled_at"
                ") VALUES (:id, 'home', 80, 80, :now, :now)"
            ),
            {"id": spirit_id, "now": NOW},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirit_emotion ("
                "spirit_id, mood, realtime_emotion, emotion_reason_code, "
                "emotion_started_at, emotion_expires_at, primary_label"
                ") VALUES ("
                ":id, 60, 'hurt', 'EVENT_RECALL', :now, :expires, 'hurt')"
            ),
            {"id": spirit_id, "now": NOW, "expires": NOW + timedelta(hours=2)},
        )
    await engine.dispose()


async def _row(url: str, spirit_id: uuid.UUID) -> tuple[object, ...]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT e.mood, e.primary_label, e.realtime_emotion, e.emotion_expires_at, "
                    "a.hunger, a.energy "
                    "FROM public.spirit_emotion e "
                    "JOIN public.spirit_awareness a ON a.spirit_id = e.spirit_id "
                    "WHERE e.spirit_id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return tuple(row)
