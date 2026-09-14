from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.speech import TTS_CACHE_TTL, TTS_MAX_CHARS, TTS_OUTPUT_MIME, m4a_fixture_bytes
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
from app.schemas.spirit import CreateSpiritRequest
from app.services.speech import synthesize_audio
from app.services.spirit import create_spirit_if_absent
from pytest import raises
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
BODY = m4a_fixture_bytes(duration_ms=800)
SIGNING_KEY = Settings(app_env="test").cursor_signing_key()


class _TtsProvider:
    source = "stub"

    def __init__(self, audio: AudioResult | None = None, *, error: str | None = None) -> None:
        self._audio = audio or AudioResult(
            body=BODY,
            mime=TTS_OUTPUT_MIME,
            duration_ms=800,
            provider_request_id="tts-1",
        )
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
        return self._audio

    async def vision(self, value: VisionInput) -> VisionOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def moderate(self, value: SafetyInput) -> SafetyOutput:
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


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _tts_row(url: str, owner_id: uuid.UUID) -> tuple[int, int]:
    probe = create_async_engine(url)
    async with probe.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT used, reserved FROM public.daily_usage "
                    "WHERE user_id = :id AND capability = 'tts'"
                ),
                {"id": owner_id},
            )
        ).first()
    await probe.dispose()
    if row is None:
        return 0, 0
    return int(row.used), int(row.reserved)


async def _tts_used_total(url: str, owner_id: uuid.UUID) -> int:
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


async def _insert_message(
    url: str,
    *,
    message_id: uuid.UUID,
    spirit_id: uuid.UUID,
    role: str,
    content: str,
    status: str,
    client_id: uuid.UUID | None,
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, client_id, role, content, source, status"
                ") VALUES ("
                ":id, :spirit_id, :client_id, :role, :content, 'text', :status)"
            ),
            {
                "id": message_id,
                "spirit_id": spirit_id,
                "client_id": client_id,
                "role": role,
                "content": content,
                "status": status,
            },
        )
    await engine.dispose()


def test_synthesize_counts_quota_once_and_cache_hit() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_success_cache_and_edges(url))


async def _assert_success_cache_and_edges(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    other = uuid.uuid4()
    await _insert_auth_user(url, owner)
    await _insert_auth_user(url, other)
    user = CurrentUser(id=owner)
    other_user = CurrentUser(id=other)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
    async with claimed_transaction(factory, other_user) as session:
        await create_spirit_if_absent(session, other_user, _spirit_request())
    message_id = uuid.uuid4()
    await _insert_message(
        url,
        message_id=message_id,
        spirit_id=created.spirit_id,
        role="spirit",
        content="你好，我是灵。",
        status="generated",
        client_id=None,
    )
    storage = PrivateTtsStorage()
    provider = _TtsProvider()
    first = await synthesize_audio(
        factory,
        user,
        client_id=uuid.uuid4(),
        message_id=message_id,
        voice_profile="default",
        now=NOW,
        storage=storage,
        signing_key=SIGNING_KEY,
        provider=provider,
    )
    assert first.cache_hit is False
    assert first.replayed is False
    assert first.mime == TTS_OUTPUT_MIME
    assert first.duration_ms == 800
    assert str(owner) in first.audio_url
    assert first.audio_url.startswith("https://kelin.invalid/object/kelin-tts/tts/")
    assert first.quotas[0].capability == "tts"
    assert first.quotas[0].used == 1
    assert first.quotas[0].limit == 20
    assert provider.calls == 1
    assert provider.texts == ["你好，我是灵。"]
    used, reserved = await _tts_row(url, owner)
    assert used == 1
    assert reserved == 0

    cached = await synthesize_audio(
        factory,
        user,
        client_id=uuid.uuid4(),
        message_id=message_id,
        voice_profile="default",
        now=NOW,
        storage=storage,
        signing_key=SIGNING_KEY,
        provider=provider,
    )
    assert cached.cache_hit is True
    assert cached.quotas[0].used == 1
    assert provider.calls == 1
    used, reserved = await _tts_row(url, owner)
    assert used == 1
    assert reserved == 0

    with raises(ApiError) as missing:
        await synthesize_audio(
            factory,
            other_user,
            client_id=uuid.uuid4(),
            message_id=message_id,
            voice_profile="default",
            now=NOW,
            storage=storage,
            signing_key=SIGNING_KEY,
            provider=provider,
        )
    assert missing.value.code == "NOT_FOUND"
    assert missing.value.status_code == 404
    other_used, other_reserved = await _tts_row(url, other)
    assert other_used == 0
    assert other_reserved == 0

    user_message = uuid.uuid4()
    await _insert_message(
        url,
        message_id=user_message,
        spirit_id=created.spirit_id,
        role="user",
        content="我的话",
        status="accepted",
        client_id=uuid.uuid4(),
    )
    with raises(ApiError) as user_role:
        await synthesize_audio(
            factory,
            user,
            client_id=uuid.uuid4(),
            message_id=user_message,
            voice_profile="default",
            now=NOW,
            storage=storage,
            signing_key=SIGNING_KEY,
            provider=provider,
        )
    assert user_role.value.code == "NOT_FOUND"
    used, reserved = await _tts_row(url, owner)
    assert used == 1
    assert reserved == 0

    long_id = uuid.uuid4()
    await _insert_message(
        url,
        message_id=long_id,
        spirit_id=created.spirit_id,
        role="spirit",
        content="字" * (TTS_MAX_CHARS + 1),
        status="generated",
        client_id=None,
    )
    with raises(ApiError) as too_long:
        await synthesize_audio(
            factory,
            user,
            client_id=uuid.uuid4(),
            message_id=long_id,
            voice_profile="default",
            now=NOW,
            storage=storage,
            signing_key=SIGNING_KEY,
            provider=provider,
        )
    assert too_long.value.code == "INVALID_INPUT"
    used, reserved = await _tts_row(url, owner)
    assert used == 1
    assert reserved == 0
    await engine.dispose()


def test_synthesize_timeout_releases_and_cache_expiry_counts_again() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_timeout_and_expiry(url))


async def _assert_timeout_and_expiry(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
    message_id = uuid.uuid4()
    await _insert_message(
        url,
        message_id=message_id,
        spirit_id=created.spirit_id,
        role="spirit",
        content="再读一遍。",
        status="generated",
        client_id=None,
    )
    storage = PrivateTtsStorage()
    timeout_id = uuid.uuid4()
    with raises(ApiError) as timed_out:
        await synthesize_audio(
            factory,
            user,
            client_id=timeout_id,
            message_id=message_id,
            voice_profile="default",
            now=NOW,
            storage=storage,
            signing_key=SIGNING_KEY,
            provider=_TtsProvider(error="PROVIDER_TIMEOUT"),
        )
    assert timed_out.value.code == "PROVIDER_TIMEOUT"
    assert timed_out.value.status_code == 504
    used, reserved = await _tts_row(url, owner)
    assert used == 0
    assert reserved == 0

    provider = _TtsProvider()
    first = await synthesize_audio(
        factory,
        user,
        client_id=timeout_id,
        message_id=message_id,
        voice_profile="default",
        now=NOW,
        storage=storage,
        signing_key=SIGNING_KEY,
        provider=provider,
    )
    assert first.cache_hit is False
    assert first.quotas[0].used == 1
    assert provider.calls == 1

    expired = await synthesize_audio(
        factory,
        user,
        client_id=uuid.uuid4(),
        message_id=message_id,
        voice_profile="default",
        now=NOW + TTS_CACHE_TTL + timedelta(seconds=1),
        storage=storage,
        signing_key=SIGNING_KEY,
        provider=provider,
    )
    assert expired.cache_hit is False
    assert expired.quotas[0].used == 1
    assert provider.calls == 2
    assert await _tts_used_total(url, owner) == 2
    await engine.dispose()
