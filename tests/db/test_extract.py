from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.providers.errors import ProviderError
from app.providers.types import (
    ASRInput,
    AudioResult,
    ChatInput,
    ChatOutput,
    ExtractInput,
    ExtractMemoryDraft,
    ExtractOutput,
    PersonalityDelta,
    SearchInput,
    SearchResult,
    Transcript,
    TTSInput,
)
from app.schemas.chat import ChatRequest
from app.schemas.extract import ExtractRequest
from app.schemas.onboarding import CompleteOnboardingRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import settle_chat_turn
from app.services.extract import settle_extract
from app.services.onboarding import complete_onboarding
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


class _FixedExtractProvider:
    source = "stub"

    def __init__(self, output: ExtractOutput) -> None:
        self._output = output

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        return self._output

    async def transcribe(self, value: ASRInput) -> Transcript:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def synthesize(self, value: TTSInput) -> AudioResult:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def vision(self, value: object) -> object:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def moderate(self, value: object) -> object:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def search(self, value: SearchInput) -> list[SearchResult]:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")


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


async def _ready_window(session: AsyncSession, user: CurrentUser) -> uuid.UUID:
    await _hatch(session, user)
    window_id: uuid.UUID | None = None
    for index in range(3):
        turned = await settle_chat_turn(
            session, user, _chat_request(onboarding=False, content=f"第{index + 1}轮"), now=NOW
        )
        window_id = turned.conversation_window_id
    assert window_id is not None
    assert turned.should_extract is True
    return window_id


def test_repeat_extract_does_not_duplicate_memory_or_trait() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_repeat_extract(url))


async def _assert_repeat_extract(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    client_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        window_id = await _ready_window(session, user)
        request = ExtractRequest(client_id=client_id, conversation_window_id=window_id)
        first = await settle_extract(session, user, request, now=NOW)
        second = await settle_extract(session, user, request, now=NOW)
        counts = (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM public.memories "
                    "WHERE spirit_id = :spirit_id) AS memories, "
                    "(SELECT count(*) FROM public.style_samples "
                    "WHERE spirit_id = :spirit_id) AS styles, "
                    "closeness, curiosity FROM public.spirits WHERE id = :spirit_id"
                ),
                {"spirit_id": first.spirit.spirit_id},
            )
        ).one()

    assert first.status == "extracted"
    assert second.status == "extracted"
    assert second.replayed is True
    assert len(first.memories) <= 2
    assert len(first.style_samples) <= 1
    assert [item.id for item in first.memories] == [item.id for item in second.memories]
    assert [item.id for item in first.style_samples] == [item.id for item in second.style_samples]
    assert first.spirit.closeness == 66
    assert second.spirit.closeness == 66
    assert counts.memories == len(first.memories)
    assert counts.styles == len(first.style_samples)
    assert int(counts.closeness) == 66
    await engine.dispose()


def test_low_confidence_extract_skips_memory_and_trait() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_low_confidence(url))


async def _assert_low_confidence(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    provider = _FixedExtractProvider(
        ExtractOutput(
            memories=[
                ExtractMemoryDraft(
                    type="preference",
                    summary="弱信号",
                    salience=10,
                    confidence=0.74,
                    personality_delta=PersonalityDelta(dimension="closeness", value=2),
                )
            ]
        )
    )
    async with claimed_transaction(factory, user) as session:
        window_id = await _ready_window(session, user)
        settled = await settle_extract(
            session,
            user,
            ExtractRequest(client_id=uuid.uuid4(), conversation_window_id=window_id),
            now=NOW,
            provider=provider,
        )
        closeness = await session.scalar(
            text("SELECT closeness FROM public.spirits WHERE id = :id"),
            {"id": settled.spirit.spirit_id},
        )
        memory_count = await session.scalar(
            text("SELECT count(*) FROM public.memories WHERE spirit_id = :id"),
            {"id": settled.spirit.spirit_id},
        )
    assert settled.status == "extracted"
    assert settled.memories == ()
    assert int(closeness) == 65
    assert int(memory_count) == 0
    await engine.dispose()


def test_extract_client_id_cannot_claim_two_windows() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_client_id_conflict(url))


async def _assert_client_id_conflict(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    client_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        first_window = await _ready_window(session, user)
        await settle_extract(
            session,
            user,
            ExtractRequest(client_id=client_id, conversation_window_id=first_window),
            now=NOW,
        )
        second_window: uuid.UUID | None = None
        for index in range(3):
            turned = await settle_chat_turn(
                session,
                user,
                _chat_request(onboarding=False, content=f"下一窗{index + 1}"),
                now=NOW,
            )
            second_window = turned.conversation_window_id
        assert second_window is not None
        assert second_window != first_window
        try:
            await settle_extract(
                session,
                user,
                ExtractRequest(client_id=client_id, conversation_window_id=second_window),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "CONFLICT"
        else:
            raise AssertionError("same extract client_id must not claim two windows")
    await engine.dispose()


def test_open_window_is_invalid_input() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_open_window(url))


async def _assert_open_window(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        await _hatch(session, user)
        opened = await settle_chat_turn(
            session, user, _chat_request(onboarding=False, content="第一轮"), now=NOW
        )
        try:
            await settle_extract(
                session,
                user,
                ExtractRequest(
                    client_id=uuid.uuid4(),
                    conversation_window_id=opened.conversation_window_id,
                ),
                now=NOW,
            )
        except ApiError as exc:
            assert exc.code == "INVALID_INPUT"
        else:
            raise AssertionError("open windows must not extract")
    await engine.dispose()


def test_extracting_window_returns_processing() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_processing(url))


async def _assert_processing(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user_id = uuid.uuid4()
    await _insert_auth_user(url, user_id)
    user = CurrentUser(id=user_id)
    client_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        window_id = await _ready_window(session, user)
        await session.execute(
            text(
                "UPDATE public.conversation_windows "
                "SET status = 'extracting', extract_client_id = :client_id "
                "WHERE id = :id"
            ),
            {"id": window_id, "client_id": client_id},
        )
        settled = await settle_extract(
            session,
            user,
            ExtractRequest(client_id=client_id, conversation_window_id=window_id),
            now=NOW,
        )
    assert settled.status == "processing"
    assert settled.memories == ()
    await engine.dispose()
