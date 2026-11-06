"""S5-T03: under 3 rounds no extract task; one window one task; extract fail keeps chat."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.errors import ProviderError
from app.providers.types import ExtractInput, ExtractOutput
from app.services.central import publish_dialogue_completed
from app.services.memory.extract_worker import process_due_extracts
from app.services.memory.window_service import EXTRACT_EVENT, DialogueCompletedFact
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
INVITE = "T5WND234"


class _FailExtract:
    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")


class _EmptyExtract:
    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        return ExtractOutput()


async def _seed_spirit(url: str, user_id: uuid.UUID, spirit_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
        await conn.execute(
            text(
                "INSERT INTO public.spirits ("
                "id, user_id, client_id, egg, invite_code, "
                "closeness, curiosity, sharpness, nocturnal, stubborn, hatched_at"
                ") VALUES ("
                ":id, :user_id, :client_id, 'warm', :invite, 50, 50, 50, 50, 50, :now)"
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


async def _seed_window(
    url: str,
    *,
    spirit_id: uuid.UUID,
    window_id: uuid.UUID,
    rounds: int,
    status: str,
    onboarding: bool = False,
) -> uuid.UUID:
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
                ":id, :spirit_id, :start_id, :end_id, :rounds, :onboarding, :status)"
            ),
            {
                "id": window_id,
                "spirit_id": spirit_id,
                "start_id": user_message,
                "end_id": spirit_message,
                "rounds": rounds,
                "onboarding": onboarding,
                "status": status,
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
            {
                "id": spirit_message,
                "spirit_id": spirit_id,
                "window_id": window_id,
            },
        )
    await engine.dispose()
    return user_message


async def _outbox_count(url: str, window_id: uuid.UUID) -> int:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text(
                "SELECT count(*) FROM public.outbox_events "
                "WHERE event_type = :event_type AND aggregate_id = :window_id"
            ),
            {"event_type": EXTRACT_EVENT, "window_id": window_id},
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


async def _message_count(url: str, window_id: uuid.UUID) -> int:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text(
                "SELECT count(*) FROM public.messages WHERE conversation_window_id = :id"
            ),
            {"id": window_id},
        )
    await engine.dispose()
    return int(value or 0)


def test_under_three_rounds_does_not_create_extract_task() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_under_three(url))


async def _assert_under_three(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    await _seed_spirit(url, user_id, spirit_id)
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    for rounds in (1, 2):
        window_id = uuid.uuid4()
        await _seed_window(
            url, spirit_id=spirit_id, window_id=window_id, rounds=rounds, status="open"
        )
        async with claimed_transaction(factory, user) as session:
            result = await publish_dialogue_completed(
                session,
                user,
                DialogueCompletedFact(
                    event_id=uuid.uuid4(),
                    spirit_id=spirit_id,
                    conversation_window_id=window_id,
                    occurred_at=NOW,
                ),
            )
        assert result is not None
        assert result.task_created is False
        assert await _outbox_count(url, window_id) == 0
        assert await _window_status(url, window_id) == "open"
    await engine.dispose()


def test_ready_window_creates_one_extract_task() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_one_task(url))


async def _assert_one_task(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    window_id = uuid.uuid4()
    await _seed_spirit(url, user_id, spirit_id)
    await _seed_window(
        url, spirit_id=spirit_id, window_id=window_id, rounds=3, status="ready"
    )
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    fact = DialogueCompletedFact(
        event_id=uuid.uuid4(),
        spirit_id=spirit_id,
        conversation_window_id=window_id,
        occurred_at=NOW,
    )
    async with claimed_transaction(factory, user) as session:
        first = await publish_dialogue_completed(session, user, fact)
    async with claimed_transaction(factory, user) as session:
        second = await publish_dialogue_completed(session, user, fact)
    onboard_id = uuid.uuid4()
    await _seed_window(
        url,
        spirit_id=spirit_id,
        window_id=onboard_id,
        rounds=3,
        status="open",
        onboarding=True,
    )
    async with claimed_transaction(factory, user) as session:
        onboard = await publish_dialogue_completed(
            session,
            user,
            DialogueCompletedFact(
                event_id=uuid.uuid4(),
                spirit_id=spirit_id,
                conversation_window_id=onboard_id,
                occurred_at=NOW,
            ),
        )
    assert first is not None and first.task_created is True
    assert second is not None and second.task_created is False
    assert onboard is not None and onboard.task_created is False
    assert await _outbox_count(url, window_id) == 1
    assert await _outbox_count(url, onboard_id) == 0
    await engine.dispose()


def test_extract_failure_does_not_rollback_chat_messages() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_extract_fail_keeps_chat(url))


async def _assert_extract_fail_keeps_chat(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    window_id = uuid.uuid4()
    await _seed_spirit(url, user_id, spirit_id)
    await _seed_window(
        url, spirit_id=spirit_id, window_id=window_id, rounds=3, status="ready"
    )
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        created = await publish_dialogue_completed(
            session,
            user,
            DialogueCompletedFact(
                event_id=uuid.uuid4(),
                spirit_id=spirit_id,
                conversation_window_id=window_id,
                occurred_at=NOW,
            ),
        )
    assert created is not None and created.task_created is True
    before = await _message_count(url, window_id)
    ticks = await process_due_extracts(factory, now=NOW, provider=_FailExtract())  # type: ignore[arg-type]
    assert ticks
    assert ticks[0].status == "failed"
    assert await _window_status(url, window_id) == "failed"
    assert await _message_count(url, window_id) == before == 2
    assert await _outbox_count(url, window_id) == 1
    async with claimed_transaction(factory, user) as session:
        again = await publish_dialogue_completed(
            session,
            user,
            DialogueCompletedFact(
                event_id=uuid.uuid4(),
                spirit_id=spirit_id,
                conversation_window_id=window_id,
                occurred_at=NOW,
            ),
        )
    assert again is not None and again.task_created is False
    await engine.dispose()


def test_extract_success_marks_extracted_without_second_task() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_extract_success(url))


async def _assert_extract_success(url: str) -> None:
    user_id = uuid.uuid4()
    spirit_id = uuid.uuid4()
    window_id = uuid.uuid4()
    await _seed_spirit(url, user_id, spirit_id)
    await _seed_window(
        url, spirit_id=spirit_id, window_id=window_id, rounds=3, status="ready"
    )
    user = CurrentUser(id=user_id)
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    async with claimed_transaction(factory, user) as session:
        created = await publish_dialogue_completed(
            session,
            user,
            DialogueCompletedFact(
                event_id=uuid.uuid4(),
                spirit_id=spirit_id,
                conversation_window_id=window_id,
                occurred_at=NOW,
            ),
        )
    assert created is not None and created.task_created is True
    ticks = await process_due_extracts(
        factory, now=NOW, provider=_EmptyExtract()  # type: ignore[arg-type]
    )
    assert ticks
    assert ticks[0].status == "extracted"
    assert await _window_status(url, window_id) == "extracted"
    assert await _message_count(url, window_id) == 2
    async with claimed_transaction(factory, user) as session:
        again = await publish_dialogue_completed(
            session,
            user,
            DialogueCompletedFact(
                event_id=uuid.uuid4(),
                spirit_id=spirit_id,
                conversation_window_id=window_id,
                occurred_at=NOW,
            ),
        )
    assert again is not None and again.task_created is False
    assert await _outbox_count(url, window_id) == 1
    await engine.dispose()
