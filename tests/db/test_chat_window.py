from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.schemas.chat import ChatRequest
from app.schemas.onboarding import CompleteOnboardingRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import settle_chat_turn
from app.services.onboarding import complete_onboarding
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
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
    )


def _chat_request(*, onboarding: bool, content: str = "你好") -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(uuid.uuid4()),
            "content": content,
            "source": "text",
            "onboarding": onboarding,
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 21,
                "weather": "cloudy",
                "city": None,
            },
        }
    )


def _complete_request(*, version: int) -> CompleteOnboardingRequest:
    return CompleteOnboardingRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "expected_spirit_version": version,
        }
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _hatch(session: AsyncSession, user: CurrentUser) -> tuple[uuid.UUID, int]:
    created = await create_spirit_if_absent(session, user, _spirit_request())
    version = created.version
    for _ in range(5):
        turned = await settle_chat_turn(session, user, _chat_request(onboarding=True), now=NOW)
        version = turned.version
    hatched = await complete_onboarding(session, user, _complete_request(version=version), now=NOW)
    assert hatched.spirit.hatched_at is not None
    return created.spirit_id, hatched.spirit.version


async def _ordinary_windows(session: AsyncSession, spirit_id: uuid.UUID) -> list[Any]:
    result = await session.execute(
        text(
            "SELECT id, start_message_id, end_message_id, user_round_count, status, onboarding "
            "FROM public.conversation_windows "
            "WHERE spirit_id = :id AND onboarding = false "
            "ORDER BY created_at ASC, id ASC"
        ),
        {"id": spirit_id},
    )
    return list(result.all())


async def _ordinary_messages(session: AsyncSession, spirit_id: uuid.UUID) -> list[Any]:
    result = await session.execute(
        text(
            "SELECT id, role, conversation_window_id "
            "FROM public.messages "
            "WHERE spirit_id = :id AND onboarding = false AND role IN ('user', 'spirit') "
            "ORDER BY created_at ASC, id ASC"
        ),
        {"id": spirit_id},
    )
    return list(result.all())


def test_three_ordinary_rounds_share_ready_window() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_ordinary_window_ready(url))


async def _assert_ordinary_window_ready(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    third_request = _chat_request(onboarding=False, content="第三轮")
    async with claimed_transaction(factory, user) as session:
        spirit_id, _version = await _hatch(session, user)
        first = await settle_chat_turn(
            session, user, _chat_request(onboarding=False, content="第一轮"), now=NOW
        )
        second = await settle_chat_turn(
            session, user, _chat_request(onboarding=False, content="第二轮"), now=NOW
        )
        third = await settle_chat_turn(session, user, third_request, now=NOW)
        windows = await _ordinary_windows(session, spirit_id)
        messages = await _ordinary_messages(session, spirit_id)
        replayed = await settle_chat_turn(session, user, third_request, now=NOW)
        fourth = await settle_chat_turn(
            session, user, _chat_request(onboarding=False, content="第四轮"), now=NOW
        )
        after_fourth = await _ordinary_windows(session, spirit_id)
        after_messages = await _ordinary_messages(session, spirit_id)

    assert first.should_extract is False
    assert second.should_extract is False
    assert third.should_extract is True
    assert first.conversation_window_id == second.conversation_window_id
    assert second.conversation_window_id == third.conversation_window_id
    assert first.ordinary_dialogue_rounds == 1
    assert second.ordinary_dialogue_rounds == 2
    assert third.ordinary_dialogue_rounds == 3
    assert len(windows) == 1
    window = windows[0]
    assert window.id == third.conversation_window_id
    assert window.user_round_count == 3
    assert window.status == "ready"
    assert window.onboarding is False
    assert window.start_message_id == first.user_message_id
    assert window.end_message_id == third.spirit_message_id
    assert {row.id for row in messages} == {
        first.user_message_id,
        first.spirit_message_id,
        second.user_message_id,
        second.spirit_message_id,
        third.user_message_id,
        third.spirit_message_id,
    }
    assert {row.conversation_window_id for row in messages} == {third.conversation_window_id}
    assert replayed.replayed is True
    assert replayed.should_extract is True
    assert replayed.conversation_window_id == third.conversation_window_id
    assert fourth.should_extract is False
    assert fourth.conversation_window_id != third.conversation_window_id
    assert fourth.ordinary_dialogue_rounds == 4
    assert len(after_fourth) == 2
    by_id = {row.id: row for row in after_fourth}
    ready = by_id[third.conversation_window_id]
    opened = by_id[fourth.conversation_window_id]
    assert ready.status == "ready"
    assert ready.user_round_count == 3
    assert opened.status == "open"
    assert opened.user_round_count == 1
    assert opened.start_message_id == fourth.user_message_id
    assert opened.end_message_id == fourth.spirit_message_id
    fourth_rows = [
        row for row in after_messages if row.conversation_window_id == fourth.conversation_window_id
    ]
    assert {row.id for row in fourth_rows} == {fourth.user_message_id, fourth.spirit_message_id}
    await engine.dispose()


def test_onboarding_rounds_do_not_mark_extract_ready() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_onboarding_not_ready(url))


async def _assert_onboarding_not_ready(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        window_ids: list[uuid.UUID] = []
        for _ in range(3):
            turned = await settle_chat_turn(session, user, _chat_request(onboarding=True), now=NOW)
            assert turned.should_extract is False
            window_ids.append(turned.conversation_window_id)
        statuses = list(
            (
                await session.execute(
                    text(
                        "SELECT status, onboarding, user_round_count "
                        "FROM public.conversation_windows WHERE spirit_id = :id "
                        "ORDER BY created_at ASC, id ASC"
                    ),
                    {"id": created.spirit_id},
                )
            ).all()
        )
    assert len(set(window_ids)) == 3
    assert statuses == [("open", True, 1), ("open", True, 1), ("open", True, 1)]
    await engine.dispose()
