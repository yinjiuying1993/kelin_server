from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.errors import ApiError
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
LATER = NOW + timedelta(hours=2)


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


def _chat_request() -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(uuid.uuid4()),
            "content": "你好",
            "source": "text",
            "onboarding": True,
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 21,
                "weather": "cloudy",
                "city": None,
            },
        }
    )


def _complete_request(
    *, version: int, client_id: uuid.UUID | None = None
) -> CompleteOnboardingRequest:
    return CompleteOnboardingRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "expected_spirit_version": version,
        }
    )


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _hatch_row(
    session: AsyncSession, user_id: uuid.UUID
) -> tuple[int, object, object, int, int]:
    row = (
        await session.execute(
            text(
                "SELECT onboarding_step, hatched_at, onboarding_completed_at, "
                "ordinary_dialogue_rounds, version "
                "FROM public.spirits WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
    ).one()
    return (
        int(row.onboarding_step),
        row.hatched_at,
        row.onboarding_completed_at,
        int(row.ordinary_dialogue_rounds),
        int(row.version),
    )


def test_step_four_and_fake_step_five_are_incomplete() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_incomplete_paths(url))


async def _assert_incomplete_paths(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        version = created.version
        for _ in range(4):
            turned = await settle_chat_turn(session, user, _chat_request(), now=NOW)
            version = turned.version
        with pytest.raises(ApiError) as step_four:
            await complete_onboarding(session, user, _complete_request(version=version), now=NOW)
        assert step_four.value.code == "ONBOARDING_INCOMPLETE"
        row = await _hatch_row(session, user_id)
        assert row[0] == 4
        assert row[1] is None
        assert row[2] is None

        await session.execute(
            text("UPDATE public.spirits SET onboarding_step = 5 WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        with pytest.raises(ApiError) as fake_step:
            await complete_onboarding(session, user, _complete_request(version=version), now=NOW)
        assert fake_step.value.code == "ONBOARDING_INCOMPLETE"
        row = await _hatch_row(session, user_id)
        assert row[1] is None
        assert row[2] is None
    await engine.dispose()


def test_step_five_writes_hatched_at_once_and_replays() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_hatch_and_replay(url))


async def _assert_hatch_and_replay(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    client_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        version = created.version
        for _ in range(5):
            turned = await settle_chat_turn(session, user, _chat_request(), now=NOW)
            version = turned.version
        with pytest.raises(ApiError) as conflict:
            await complete_onboarding(
                session, user, _complete_request(version=1, client_id=client_id), now=NOW
            )
        assert conflict.value.code == "CONFLICT"
        first = await complete_onboarding(
            session, user, _complete_request(version=version, client_id=client_id), now=NOW
        )
        assert first.replayed is False
        assert first.spirit.onboarding_step == 5
        assert first.spirit.hatched_at == NOW
        assert first.spirit.onboarding_completed_at == NOW
        assert first.ordinary_dialogue_rounds == 0
        hatched = first.spirit.hatched_at
        hatched_version = first.spirit.version

    async with claimed_transaction(factory, user) as session:
        replayed = await complete_onboarding(
            session,
            user,
            _complete_request(version=version, client_id=client_id),
            now=LATER,
        )
        row = await _hatch_row(session, user_id)
        with pytest.raises(ApiError) as already:
            await complete_onboarding(
                session, user, _complete_request(version=hatched_version), now=LATER
            )
        assert already.value.code == "ONBOARDING_ALREADY_COMPLETED"
    assert replayed.replayed is True
    assert replayed.spirit.hatched_at == hatched
    assert replayed.spirit.onboarding_completed_at == hatched
    assert replayed.spirit.version == hatched_version
    assert row[1] == hatched
    assert row[2] == hatched
    assert row[3] == 0
    await engine.dispose()
