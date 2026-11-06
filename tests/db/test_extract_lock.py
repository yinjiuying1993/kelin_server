"""POST /extract releases the spirit lock before the model call."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.errors import ProviderError
from app.providers.types import ExtractInput, ExtractMemoryDraft, ExtractOutput
from app.repositories import chat as chat_repo
from app.schemas.extract import ExtractRequest
from app.services.extract import settle_extract
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "LKEX2345"
QUOTE = "记住我喜欢窗边"


class _OutsideLock:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        user: CurrentUser,
        spirit_id: uuid.UUID,
        window_id: uuid.UUID,
    ) -> None:
        self.factory = factory
        self.user = user
        self.spirit_id = spirit_id
        self.window_id = window_id
        self.calls = 0
        self.saw_extracting = False
        self.saw_lock = False
        self.mode = "bump"

    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        self.calls += 1
        if self.mode == "fail":
            raise ProviderError("MODEL_UNAVAILABLE")
        if self.mode == "bump":
            async with claimed_transaction(self.factory, self.user) as session:
                status = await session.scalar(
                    text("SELECT status FROM public.conversation_windows WHERE id = :id"),
                    {"id": self.window_id},
                )
                self.saw_extracting = status == "extracting"
                await chat_repo.lock_spirit_for_owner(session, self.user.id, nowait=True)
                self.saw_lock = True
                await session.execute(
                    text(
                        "UPDATE public.spirits SET snapshot_version = snapshot_version + 1 "
                        "WHERE id = :id"
                    ),
                    {"id": self.spirit_id},
                )
        return ExtractOutput(
            memories=[
                ExtractMemoryDraft(
                    type="preference",
                    summary="喜欢窗边",
                    normalized_topic_key="preference:user:seat:window",
                    source_quote=QUOTE,
                    signal="explicit",
                    confidence=0.9,
                )
            ]
        )


def test_extract_drops_the_lock_and_a_stale_snapshot() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_lock(url))


async def _assert_lock(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    first_window = await _seed_spirit(url, user_id, spirit_id)
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    client_id = uuid.uuid4()
    provider = _OutsideLock(factory, user, spirit_id, first_window)
    settled = await settle_extract(
        factory,
        user,
        ExtractRequest(client_id=client_id, conversation_window_id=first_window),
        now=NOW,
        provider=provider,  # type: ignore[arg-type]
    )
    assert provider.saw_extracting is True
    assert provider.saw_lock is True
    assert provider.calls == 1
    assert settled.status == "extracted"
    assert settled.memories == ()
    assert await _memory_count(url, spirit_id) == 0
    assert await _closeness(url, spirit_id) == 50

    provider.mode = "ok"
    replay = await settle_extract(
        factory,
        user,
        ExtractRequest(client_id=client_id, conversation_window_id=first_window),
        now=NOW,
        provider=provider,  # type: ignore[arg-type]
    )
    assert replay.replayed is True
    assert provider.calls == 1

    second_window = await _seed_window(url, spirit_id)
    try:
        await settle_extract(
            factory,
            user,
            ExtractRequest(client_id=client_id, conversation_window_id=second_window),
            now=NOW,
            provider=provider,  # type: ignore[arg-type]
        )
    except ApiError as exc:
        assert exc.code == "IDEMPOTENCY_CONFLICT"
    else:
        raise AssertionError("same client_id on another window must conflict")
    assert provider.calls == 1

    failed_window = await _seed_window(url, spirit_id)
    provider.window_id = failed_window
    provider.mode = "fail"
    failed_client = uuid.uuid4()
    try:
        await settle_extract(
            factory,
            user,
            ExtractRequest(client_id=failed_client, conversation_window_id=failed_window),
            now=NOW,
            provider=provider,  # type: ignore[arg-type]
        )
    except ApiError as exc:
        assert exc.code == "MODEL_UNAVAILABLE"
    else:
        raise AssertionError("model failure must not settle")
    assert await _window_status(url, failed_window) == "failed"
    assert await _memory_count(url, spirit_id) == 0
    assert await _closeness(url, spirit_id) == 50

    provider.mode = "ok"
    restored = await settle_extract(
        factory,
        user,
        ExtractRequest(client_id=failed_client, conversation_window_id=failed_window),
        now=NOW,
        provider=provider,  # type: ignore[arg-type]
    )
    assert restored.replayed is False
    assert [item.summary for item in restored.memories] == ["喜欢窗边"]
    assert await _memory_count(url, spirit_id) == 1
    assert await _closeness(url, spirit_id) == 50
    await engine.dispose()


async def _seed_spirit(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> uuid.UUID:
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
    await engine.dispose()
    return await _seed_window(url, spirit_id)


async def _seed_window(url: str, spirit_id: uuid.UUID) -> uuid.UUID:
    window_id = uuid.uuid4()
    user_message = uuid.uuid4()
    spirit_message = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
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
                ":id, :spirit_id, :window_id, :client_id, 'user', :content, 'text', 'accepted')"
            ),
            {
                "id": user_message,
                "spirit_id": spirit_id,
                "window_id": window_id,
                "client_id": uuid.uuid4(),
                "content": QUOTE,
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


async def _memory_count(url: str, spirit_id: uuid.UUID) -> int:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT count(*) FROM public.memories WHERE spirit_id = :id"),
            {"id": spirit_id},
        )
    await engine.dispose()
    return int(value or 0)


async def _closeness(url: str, spirit_id: uuid.UUID) -> int:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT closeness FROM public.spirits WHERE id = :id"),
            {"id": spirit_id},
        )
    await engine.dispose()
    return int(value or 0)


async def _window_status(url: str, window_id: uuid.UUID) -> str:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT status FROM public.conversation_windows WHERE id = :id"),
            {"id": window_id},
        )
    await engine.dispose()
    return str(value)
