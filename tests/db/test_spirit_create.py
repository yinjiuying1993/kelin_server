from __future__ import annotations

import asyncio
import uuid
from datetime import time
from typing import Any

import pytest
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.invite_code import invite_code_is_valid
from app.domain.spirit import SpiritCreateResult, traits_for_egg
from app.repositories import spirit as spirit_repo
from app.schemas.spirit import CreateSpiritRequest
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head


def _request(
    *,
    egg: str = "warm",
    name: str = "未名",
    client_id: uuid.UUID | None = None,
) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
            "egg": egg,
            "name": name,
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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _counts(session: AsyncSession, user_id: uuid.UUID) -> tuple[int, int, int]:
    spirits = await session.scalar(
        text("SELECT count(*) FROM public.spirits WHERE user_id = :id"),
        {"id": user_id},
    )
    consents = await session.scalar(
        text("SELECT count(*) FROM public.account_consents WHERE user_id = :id"),
        {"id": user_id},
    )
    prefs = await session.scalar(
        text("SELECT count(*) FROM public.user_preferences WHERE user_id = :id"),
        {"id": user_id},
    )
    return int(spirits or 0), int(consents or 0), int(prefs or 0)


def _assert_defaults(result: SpiritCreateResult, request: CreateSpiritRequest) -> None:
    traits = traits_for_egg(request.egg)
    assert result.client_id == request.client_id
    assert result.name == request.name
    assert result.egg == request.egg
    assert invite_code_is_valid(result.invite_code)
    assert result.closeness == traits.closeness
    assert result.curiosity == traits.curiosity
    assert result.sharpness == traits.sharpness
    assert result.nocturnal == traits.nocturnal
    assert result.stubborn == traits.stubborn
    assert result.hunger == 80
    assert result.energy == 80
    assert result.mood == 60
    assert result.bond == 0
    assert result.stage == "whelp"
    assert result.status == "home"
    assert result.version == 1
    assert result.onboarding_step == 0
    assert result.onboarding_completed_at is None
    assert result.hatched_at is None
    assert result.tts_on is False
    assert result.push_on is False
    assert result.visit_on is True
    assert result.dnd_start == time(23, 0)
    assert result.dnd_end == time(8, 0)
    assert result.timezone == "Asia/Shanghai"
    assert result.location_weather_on is False
    assert result.remote_search_on is True


def test_create_is_atomic_for_three_eggs_and_records_consents() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_three_eggs(url))


async def _assert_three_eggs(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    for egg in ("warm", "cold", "wild"):
        user_id = uuid.uuid4()
        await _insert_auth_user(url, user_id)
        request = _request(egg=egg)
        user = CurrentUser(id=user_id)
        async with claimed_transaction(factory, user) as session:
            result = await create_spirit_if_absent(session, user, request)
            consents = await spirit_repo.fetch_consents_for_owner(session, user_id)
            counts = await _counts(session, user_id)
        _assert_defaults(result, request)
        assert counts == (1, 3, 1)
        assert consents == [
            ("ai_disclosure", "2026-09", "explicitly_accepted"),
            ("data_notice", "2026-09", "displayed"),
            ("user_terms", "2026-09", "displayed"),
        ]
    await engine.dispose()


def test_same_client_id_replays_and_different_payload_conflicts() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_replay_and_payload_conflict(url))


async def _assert_replay_and_payload_conflict(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    first = _request(name="未名")
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, first)
    async with claimed_transaction(factory, user) as session:
        replayed = await create_spirit_if_absent(session, user, first)
        counts = await _counts(session, user_id)
    assert replayed.spirit_id == created.spirit_id
    assert replayed.invite_code == created.invite_code
    assert counts == (1, 3, 1)
    renamed = _request(name="雾生", client_id=first.client_id)
    with pytest.raises(ApiError) as conflict:
        async with claimed_transaction(factory, user) as session:
            await create_spirit_if_absent(session, user, renamed)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    other_client = _request(name="未名")
    with pytest.raises(ApiError) as already:
        async with claimed_transaction(factory, user) as session:
            await create_spirit_if_absent(session, user, other_client)
    assert already.value.code == "CONFLICT"
    async with claimed_transaction(factory, user) as session:
        assert await _counts(session, user_id) == (1, 3, 1)
    await engine.dispose()


def test_concurrent_same_client_id_creates_one_spirit() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_concurrent_same_client(url))


async def _assert_concurrent_same_client(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=8, max_overflow=4)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    request = _request()

    async def _one() -> SpiritCreateResult | BaseException:
        try:
            async with claimed_transaction(factory, user) as session:
                return await create_spirit_if_absent(session, user, request)
        except BaseException as exc:
            return exc

    outcomes = await asyncio.gather(*[_one() for _ in range(8)])
    results = [item for item in outcomes if isinstance(item, SpiritCreateResult)]
    assert len(results) == 8
    assert {item.spirit_id for item in results} == {results[0].spirit_id}
    assert {item.invite_code for item in results} == {results[0].invite_code}
    async with claimed_transaction(factory, user) as session:
        assert await _counts(session, user_id) == (1, 3, 1)
    await engine.dispose()


def test_concurrent_different_client_ids_create_one_spirit() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_concurrent_different_clients(url))


async def _assert_concurrent_different_clients(url: str) -> None:
    engine = create_runtime_engine(url, pool_size=8, max_overflow=4)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)

    async def _one() -> SpiritCreateResult | ApiError:
        request = _request()
        try:
            async with claimed_transaction(factory, user) as session:
                return await create_spirit_if_absent(session, user, request)
        except ApiError as exc:
            return exc

    outcomes = await asyncio.gather(*[_one() for _ in range(8)])
    wins = [item for item in outcomes if isinstance(item, SpiritCreateResult)]
    conflicts = [
        item for item in outcomes if isinstance(item, ApiError) and item.code == "CONFLICT"
    ]
    assert len(wins) == 1
    assert len(conflicts) == 7
    async with claimed_transaction(factory, user) as session:
        assert await _counts(session, user_id) == (1, 3, 1)
    await engine.dispose()


def test_failed_preference_insert_rolls_back_spirit_and_consents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = upgrade_empty_kelin_test_to_head()

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("preferences failed")

    monkeypatch.setattr("app.services.spirit.spirit_repo.insert_preferences", _boom)
    asyncio.run(_assert_rollback(url))


async def _assert_rollback(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    with pytest.raises(RuntimeError, match="preferences failed"):
        async with claimed_transaction(factory, user) as session:
            await create_spirit_if_absent(session, user, _request())
    async with claimed_transaction(factory, user) as session:
        assert await _counts(session, user_id) == (0, 0, 0)
    await engine.dispose()
