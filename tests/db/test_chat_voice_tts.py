from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.chat import GeneratedTurn
from app.domain.speech import TTS_OUTPUT_MIME, m4a_fixture_bytes
from app.integrations.storage import PrivateTtsStorage
from app.providers.errors import ProviderError
from app.providers.types import (
    ASRInput,
    AudioResult,
    ChatInput,
    ChatOutput,
    ExtractInput,
    ExtractOutput,
    SafetyInput,
    SafetyOutput,
    SearchInput,
    SearchResult,
    Transcript,
    TTSInput,
    VisionInput,
    VisionOutput,
)
from app.schemas.chat import ChatRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import complete_chat_turn
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
BODY = m4a_fixture_bytes(duration_ms=800)
SIGNING_KEY = Settings(app_env="test").cursor_signing_key()


class _TtsProvider:
    source = "stub"

    def __init__(self, *, error: str | None = None) -> None:
        self._error = error
        self.calls = 0
        self.texts: list[str] = []

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def transcribe(self, value: ASRInput) -> Transcript:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def synthesize(self, value: TTSInput) -> AudioResult:
        self.calls += 1
        self.texts.append(value.text)
        if self._error is not None:
            raise ProviderError(self._error)
        return AudioResult(
            body=BODY,
            mime=TTS_OUTPUT_MIME,
            duration_ms=800,
            provider_request_id="tts-chat-1",
        )

    async def vision(self, value: VisionInput) -> VisionOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def moderate(self, value: SafetyInput) -> SafetyOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def search(self, value: SearchInput) -> list[SearchResult]:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")


class _LongReply:
    def generate(self, *, onboarding: bool, onboarding_step: int) -> GeneratedTurn:
        del onboarding, onboarding_step
        return GeneratedTurn(
            content="字" * 201,
            generation_source="stub",
            input_units=0,
            output_units=0,
        )


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
                "data_notice": {
                    "document_version": "2026-09",
                    "displayed": True,
                },
                "user_terms": {
                    "document_version": "2026-09",
                    "displayed": True,
                },
            },
        }
    )


def _chat_request(
    *,
    source: str = "voice",
    client_message_id: uuid.UUID | None = None,
    content: str = "你好",
) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(client_message_id or uuid.uuid4()),
            "content": content,
            "source": source,
            "onboarding": True,
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


async def _tts_used(url: str, owner_id: uuid.UUID) -> int:
    probe = create_async_engine(url)
    async with probe.connect() as conn:
        total = await conn.scalar(
            text(
                "SELECT coalesce(sum(used), 0) FROM public.daily_usage "
                "WHERE user_id = :id AND capability = 'tts'"
            ),
            {"id": owner_id},
        )
    await probe.dispose()
    return int(total or 0)


def test_voice_chat_returns_speech_audio_and_replays_from_cache() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_voice_tts(url))


async def _assert_voice_tts(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())

    storage = PrivateTtsStorage()
    provider = _TtsProvider()
    request = _chat_request()
    first = await complete_chat_turn(
        factory,
        user,
        request,
        now=NOW,
        tts_storage=storage,
        signing_key=SIGNING_KEY,
        tts_provider=provider,
    )
    assert first.speech_audio is not None
    assert first.speech_audio.cache_hit is False
    assert first.speech_audio.mime == TTS_OUTPUT_MIME
    assert first.speech_audio.audio_url.startswith("https://kelin.invalid/object/kelin-tts/")
    assert first.spirit_content in provider.texts
    assert provider.calls == 1
    capabilities = {item.capability: item for item in first.quotas}
    assert "chat" in capabilities
    assert capabilities["tts"].used == 1
    assert capabilities["tts"].limit == 20
    assert await _tts_used(url, owner) == 1

    replayed = await complete_chat_turn(
        factory,
        user,
        request,
        now=NOW,
        tts_storage=storage,
        signing_key=SIGNING_KEY,
        tts_provider=provider,
    )
    assert replayed.replayed is True
    assert replayed.spirit_message_id == first.spirit_message_id
    assert replayed.speech_audio is not None
    assert replayed.speech_audio.audio_url
    assert provider.calls == 1
    assert await _tts_used(url, owner) == 1

    text_provider = _TtsProvider()
    text_turn = await complete_chat_turn(
        factory,
        user,
        _chat_request(source="text"),
        now=NOW,
        tts_storage=storage,
        signing_key=SIGNING_KEY,
        tts_provider=text_provider,
    )
    assert text_turn.speech_audio is None
    assert text_provider.calls == 0

    failed_provider = _TtsProvider(error="MODEL_UNAVAILABLE")
    degraded = await complete_chat_turn(
        factory,
        user,
        _chat_request(),
        now=NOW,
        tts_storage=storage,
        signing_key=SIGNING_KEY,
        tts_provider=failed_provider,
    )
    assert degraded.spirit_content
    assert degraded.speech_audio is None
    assert failed_provider.calls == 1

    too_long = await complete_chat_turn(
        factory,
        user,
        _chat_request(),
        now=NOW,
        tts_storage=storage,
        signing_key=SIGNING_KEY,
        tts_provider=_TtsProvider(),
        generator=_LongReply(),
    )
    assert len(too_long.spirit_content) == 201
    assert too_long.speech_audio is None


def test_voice_chat_without_storage_skips_tts() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_missing_storage(url))


async def _assert_missing_storage(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())
    settled = await complete_chat_turn(
        factory,
        user,
        _chat_request(),
        now=NOW,
    )
    assert settled.speech_audio is None
