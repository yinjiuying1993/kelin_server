"""S5-T06: a model personality delta is not applied and no catchphrase row is written."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import create_runtime_engine, create_session_factory
from app.providers.types import ExtractInput
from app.schemas.extract import ExtractRequest
from app.services.extract import settle_extract
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "T6EXT234"


class _DeltaExtract:
    async def extract(self, value: ExtractInput) -> object:
        del value
        return {
            "style_samples": [{"kind": "spirit_catchphrase", "text": "嘿嘿"}],
            "memories": [
                {
                    "type": "preference",
                    "summary": "叫我阿年",
                    "normalized_topic_key": "preference:user:name:nian",
                    "source_quote": "今天想被叫阿年",
                    "signal": "explicit",
                    "tags": [],
                    "salience": 80,
                    "confidence": 0.9,
                    "personality_delta": {"dimension": "closeness", "value": 2},
                }
            ],
        }


async def _seed(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> uuid.UUID:
    window_id = uuid.uuid4()
    user_message = uuid.uuid4()
    spirit_message = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text("INSERT INTO public.user_preferences (user_id) VALUES (:user_id)"),
            {"user_id": user_id},
        )
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, name, stage, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, bond, hatched_at"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, '雾生', 'whelp', "
                "50, 50, 50, 50, 50, 12, :now)"
            ),
            {
                "id": spirit_id,
                "user_id": user_id,
                "client_id": uuid.uuid4(),
                "invite": INVITE,
                "now": NOW,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.conversation_windows ("
                "id, spirit_id, start_message_id, end_message_id, "
                "user_round_count, onboarding, status"
                ") VALUES ("
                ":id, :spirit_id, :start_id, :end_id, 3, false, 'ready')"
            ),
            {
                "id": window_id,
                "spirit_id": spirit_id,
                "start_id": user_message,
                "end_id": spirit_message,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, conversation_window_id, client_id, role, content, source, status"
                ") VALUES ("
                ":id, :spirit_id, :window_id, :client_id, "
                "'user', '今天想被叫阿年', 'text', 'accepted')"
            ),
            {
                "id": user_message,
                "spirit_id": spirit_id,
                "window_id": window_id,
                "client_id": uuid.uuid4(),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, conversation_window_id, role, content, source, status"
                ") VALUES ("
                ":id, :spirit_id, :window_id, 'spirit', '记下了。', 'text', 'accepted')"
            ),
            {"id": spirit_message, "spirit_id": spirit_id, "window_id": window_id},
        )
    await engine.dispose()
    return window_id


async def _numbers(url: str, spirit_id: uuid.UUID) -> tuple[int, int, str, int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT s.closeness, s.bond, s.stage, "
                    "(SELECT count(*) FROM public.style_samples WHERE spirit_id = s.id), "
                    "(SELECT count(*) FROM public.memories WHERE spirit_id = s.id) "
                    "FROM public.spirits s WHERE s.id = :id"
                ),
                {"id": spirit_id},
            )
        ).one()
    await engine.dispose()
    return int(row[0]), int(row[1]), str(row[2]), int(row[3]), int(row[4])


def test_extract_discards_personality_delta_and_style_sample() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_discard(url))


async def _assert_discard(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    window_id = await _seed(url, user_id, spirit_id)
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    settled = await settle_extract(
        factory,
        user,
        ExtractRequest(client_id=uuid.uuid4(), conversation_window_id=window_id),
        now=NOW,
        provider=_DeltaExtract(),  # type: ignore[arg-type]
    )
    assert settled.style_samples == ()
    assert [item.summary for item in settled.memories] == ["叫我阿年"]
    assert await _numbers(url, spirit_id) == (50, 12, "whelp", 0, 1)
    await engine.dispose()
