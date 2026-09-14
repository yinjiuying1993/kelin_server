from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.chat import (
    CHAT_TURN_OPERATION,
    CHAT_TURN_RETRY_AFTER_MS,
    ChatGenerationError,
    GeneratedTurn,
    OnboardingStubGenerator,
    chat_turn_request_hash,
)
from app.repositories import chat as chat_repo
from app.schemas.chat import ChatRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import settle_chat_turn
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


class _FailingGenerator:
    def generate(self, *, onboarding: bool, onboarding_step: int) -> GeneratedTurn:
        del onboarding, onboarding_step
        raise ChatGenerationError("stub unavailable")


def _spirit_request(*, client_id: uuid.UUID | None = None) -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(client_id or uuid.uuid4()),
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


def _chat_request(
    *,
    client_message_id: uuid.UUID | None = None,
    content: str = "你好",
    onboarding: bool = True,
) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(client_message_id or uuid.uuid4()),
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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _counts(session: AsyncSession, spirit_id: uuid.UUID) -> tuple[int, int, int, int]:
    users = await session.scalar(
        text("SELECT count(*) FROM public.messages WHERE spirit_id = :id AND role = 'user'"),
        {"id": spirit_id},
    )
    spirits = await session.scalar(
        text("SELECT count(*) FROM public.messages WHERE spirit_id = :id AND role = 'spirit'"),
        {"id": spirit_id},
    )
    windows = await session.scalar(
        text("SELECT count(*) FROM public.conversation_windows WHERE spirit_id = :id"),
        {"id": spirit_id},
    )
    events = await session.scalar(
        text(
            "SELECT count(*) FROM public.growth_events "
            "WHERE spirit_id = :id AND event_type = 'chat_completed'"
        ),
        {"id": spirit_id},
    )
    return int(users or 0), int(spirits or 0), int(windows or 0), int(events or 0)


async def _spirit_counters(session: AsyncSession, user_id: uuid.UUID) -> Any:
    return (
        await session.execute(
            text(
                "SELECT onboarding_step, ordinary_dialogue_rounds, version, hatched_at "
                "FROM public.spirits WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
    ).one()


def test_complete_replay_fail_and_user_only_do_not_double_count() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_counting_invariants(url))


async def _assert_counting_invariants(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())

    failing = _chat_request()
    with pytest.raises(ApiError) as failed:
        async with claimed_transaction(factory, user) as session:
            await settle_chat_turn(session, user, failing, now=NOW, generator=_FailingGenerator())
    assert failed.value.code == "MODEL_UNAVAILABLE"
    assert failed.value.retryable is True
    async with claimed_transaction(factory, user) as session:
        counters = await _spirit_counters(session, user_id)
        counts = await _counts(session, created.spirit_id)
    assert counters.onboarding_step == 0
    assert counters.ordinary_dialogue_rounds == 0
    assert counts == (0, 0, 0, 0)

    async with claimed_transaction(factory, user) as session:
        first = await settle_chat_turn(session, user, failing, now=NOW)
        counters = await _spirit_counters(session, user_id)
        counts = await _counts(session, created.spirit_id)
    assert first.replayed is False
    assert first.onboarding is True
    assert first.generation_source == "stub"
    assert first.onboarding_step == 1
    assert first.ordinary_dialogue_rounds == 0
    assert counters.onboarding_step == 1
    assert counters.ordinary_dialogue_rounds == 0
    assert counters.hatched_at is None
    assert counts == (1, 1, 1, 1)

    async with claimed_transaction(factory, user) as session:
        replayed = await settle_chat_turn(session, user, failing, now=NOW)
        counters = await _spirit_counters(session, user_id)
        counts = await _counts(session, created.spirit_id)
    assert replayed.replayed is True
    assert replayed.user_message_id == first.user_message_id
    assert replayed.spirit_message_id == first.spirit_message_id
    assert replayed.onboarding_step == 1
    assert counters.onboarding_step == 1
    assert counters.version == first.version
    assert counts == (1, 1, 1, 1)

    conflicted = _chat_request(client_message_id=failing.client_message_id, content="换一句")
    with pytest.raises(ApiError) as conflict:
        async with claimed_transaction(factory, user) as session:
            await settle_chat_turn(session, user, conflicted, now=NOW)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"

    half = _chat_request()
    async with claimed_transaction(factory, user) as session:
        await chat_repo.insert_user_message(
            session,
            message_id=uuid.uuid4(),
            spirit_id=created.spirit_id,
            client_id=half.client_message_id,
            content=half.content,
            source=half.source,
            onboarding=True,
        )
        counters = await _spirit_counters(session, user_id)
        counts = await _counts(session, created.spirit_id)
    assert counters.onboarding_step == 1
    assert counts == (2, 1, 1, 1)

    async with claimed_transaction(factory, user) as session:
        recovered = await settle_chat_turn(session, user, half, now=NOW)
        counters = await _spirit_counters(session, user_id)
        counts = await _counts(session, created.spirit_id)
    assert recovered.replayed is False
    assert recovered.onboarding_step == 2
    assert recovered.ordinary_dialogue_rounds == 0
    assert counters.onboarding_step == 2
    assert counts == (2, 2, 2, 2)
    await engine.dispose()


def test_five_onboarding_rounds_leave_ordinary_rounds_at_zero() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_five_rounds(url))


async def _assert_five_rounds(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        step = 0
        for _ in range(5):
            result = await settle_chat_turn(session, user, _chat_request(), now=NOW)
            step = result.onboarding_step
            assert result.ordinary_dialogue_rounds == 0
        counters = await _spirit_counters(session, user_id)
        counts = await _counts(session, created.spirit_id)
    assert step == 5
    assert counters.onboarding_step == 5
    assert counters.ordinary_dialogue_rounds == 0
    assert counters.hatched_at is None
    assert counts == (5, 5, 5, 5)
    await engine.dispose()


def test_onboarding_flag_mismatch_and_readonly_are_rejected() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_flag_and_readonly(url))


async def _assert_flag_and_readonly(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())
    with pytest.raises(ApiError) as incomplete:
        async with claimed_transaction(factory, user) as session:
            await settle_chat_turn(session, user, _chat_request(onboarding=False), now=NOW)
    assert incomplete.value.code == "ONBOARDING_INCOMPLETE"

    missing = CurrentUser(id=uuid.uuid4())
    await _insert_auth_user(url, missing.id)
    with pytest.raises(ApiError) as missing_spirit:
        async with claimed_transaction(factory, missing) as session:
            await settle_chat_turn(session, missing, _chat_request(), now=NOW)
    assert missing_spirit.value.code == "NOT_FOUND"

    async with factory() as session:
        async with session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            with pytest.raises(RuntimeError, match="read-only"):
                await settle_chat_turn(session, user, _chat_request(), now=NOW)
    await engine.dispose()


class _CountingGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, *, onboarding: bool, onboarding_step: int) -> GeneratedTurn:
        self.calls += 1
        return OnboardingStubGenerator().generate(
            onboarding=onboarding, onboarding_step=onboarding_step
        )


def test_same_id_replays_once_conflict_and_expired_lease_reclaims() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_idempotency_lease(url))


async def _assert_idempotency_lease(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    request = _chat_request()
    generator = _CountingGenerator()
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        first = await settle_chat_turn(session, user, request, now=NOW, generator=generator)
    assert generator.calls == 1
    async with claimed_transaction(factory, user) as session:
        replayed = await settle_chat_turn(session, user, request, now=NOW, generator=generator)
        counts = await _counts(session, created.spirit_id)
        status = await session.scalar(
            text(
                "SELECT status FROM public.idempotency_records "
                "WHERE user_id = :user_id AND operation = :operation "
                "AND client_id = :client_id"
            ),
            {
                "user_id": user_id,
                "operation": CHAT_TURN_OPERATION,
                "client_id": request.client_message_id,
            },
        )
    assert replayed.replayed is True
    assert replayed.user_message_id == first.user_message_id
    assert replayed.spirit_message_id == first.spirit_message_id
    assert generator.calls == 1
    assert counts == (1, 1, 1, 1)
    assert status == "completed"

    conflicted = _chat_request(client_message_id=request.client_message_id, content="换一句")
    with pytest.raises(ApiError) as conflict:
        async with claimed_transaction(factory, user) as session:
            await settle_chat_turn(session, user, conflicted, now=NOW)
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"

    expired = _chat_request()
    async with claimed_transaction(factory, user) as session:
        await session.execute(
            text(
                "INSERT INTO public.idempotency_records ("
                "user_id, operation, client_id, request_hash, status, locked_until"
                ") VALUES ("
                ":user_id, :operation, :client_id, :request_hash, 'in_progress', :locked_until)"
            ),
            {
                "user_id": user_id,
                "operation": CHAT_TURN_OPERATION,
                "client_id": expired.client_message_id,
                "request_hash": chat_turn_request_hash(expired),
                "locked_until": NOW - timedelta(seconds=1),
            },
        )
    async with claimed_transaction(factory, user) as session:
        recovered = await settle_chat_turn(session, user, expired, now=NOW)
        counts = await _counts(session, created.spirit_id)
    assert recovered.replayed is False
    assert counts == (2, 2, 2, 2)
    await engine.dispose()


def test_overlapping_same_id_and_generate_lease_return_in_progress() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_concurrent_leases(url))


async def _assert_concurrent_leases(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    request = _chat_request()
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())

    started = asyncio.Event()
    release = asyncio.Event()

    async def _hold_same_id() -> None:
        try:
            async with claimed_transaction(factory, user) as session:
                claim = await chat_repo.claim_turn_idempotency(
                    session,
                    user.id,
                    request.client_message_id,
                    chat_turn_request_hash(request),
                    locked_until=NOW + timedelta(seconds=120),
                )
                assert claim.inserted is True
                started.set()
                await release.wait()
                raise RuntimeError("hold-only")
        except RuntimeError as exc:
            if str(exc) != "hold-only":
                raise

    async def _challenge_same_id() -> ApiError:
        await started.wait()
        try:
            async with claimed_transaction(factory, user) as session:
                await settle_chat_turn(session, user, request, now=NOW)
        except ApiError as exc:
            return exc
        raise AssertionError("overlapping same id must be in progress")

    holder = asyncio.create_task(_hold_same_id())
    challenged = await _challenge_same_id()
    assert challenged.code == "IDEMPOTENCY_IN_PROGRESS"
    assert challenged.retryable is True
    assert challenged.details == {"retry_after_ms": CHAT_TURN_RETRY_AFTER_MS}
    release.set()
    await holder

    async with claimed_transaction(factory, user) as session:
        first = await settle_chat_turn(session, user, request, now=NOW)
        counts = await _counts(session, created.spirit_id)
    assert first.replayed is False
    assert counts == (1, 1, 1, 1)

    other = _chat_request()
    spirit_started = asyncio.Event()
    spirit_release = asyncio.Event()

    async def _hold_spirit() -> None:
        async with claimed_transaction(factory, user) as session:
            locked = await chat_repo.lock_spirit_for_owner(session, user.id)
            assert locked is not None
            spirit_started.set()
            await spirit_release.wait()

    async def _challenge_other_id() -> ApiError:
        await spirit_started.wait()
        try:
            async with claimed_transaction(factory, user) as session:
                await settle_chat_turn(session, user, other, now=NOW)
        except ApiError as exc:
            return exc
        raise AssertionError("busy generate lease must be in progress")

    holder = asyncio.create_task(_hold_spirit())
    busy = await _challenge_other_id()
    assert busy.code == "IDEMPOTENCY_IN_PROGRESS"
    assert busy.retryable is True
    spirit_release.set()
    await holder

    async with claimed_transaction(factory, user) as session:
        second = await settle_chat_turn(session, user, other, now=NOW)
        counts = await _counts(session, created.spirit_id)
    assert second.user_message_id != first.user_message_id
    assert counts == (2, 2, 2, 2)
    await engine.dispose()
