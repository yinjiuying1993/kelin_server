from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.speech import m4a_fixture_bytes
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
from app.services.speech import transcribe_audio
from app.services.spirit import create_spirit_if_absent
from pytest import raises
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
BODY = m4a_fixture_bytes(duration_ms=1200)


class _AsrProvider:
    source = "stub"

    def __init__(self, transcript: Transcript | None = None, *, error: str | None = None) -> None:
        self._transcript = transcript or Transcript(
            text="你好", language="zh", provider_request_id="asr-1"
        )
        self._error = error
        self.calls = 0

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def extract(self, value: ExtractInput) -> ExtractOutput:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

    async def transcribe(self, value: ASRInput) -> Transcript:
        self.calls += 1
        del value
        if self._error is not None:
            raise ProviderError(self._error)
        return self._transcript

    async def synthesize(self, value: TTSInput) -> AudioResult:
        del value
        raise ProviderError("MODEL_UNAVAILABLE")

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


async def _asr_row(url: str, owner_id: uuid.UUID) -> tuple[int, int]:
    probe = create_async_engine(url)
    async with probe.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT used, reserved FROM public.daily_usage "
                    "WHERE user_id = :id AND capability = 'asr'"
                ),
                {"id": owner_id},
            )
        ).first()
    await probe.dispose()
    if row is None:
        return 0, 0
    return int(row.used), int(row.reserved)


def test_transcribe_counts_quota_once_and_replays() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_success_and_replay(url))


async def _assert_success_and_replay(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())
    client_id = uuid.uuid4()
    provider = _AsrProvider()
    first = await transcribe_audio(
        factory,
        user,
        client_id=client_id,
        mime_type="audio/mp4",
        body=BODY,
        now=NOW,
        provider=provider,
    )
    assert first.text == "你好"
    assert first.duration_ms == 1200
    assert first.language == "zh"
    assert first.replayed is False
    assert first.quotas[0].capability == "asr"
    assert first.quotas[0].used == 1
    assert first.quotas[0].limit == 60
    assert provider.calls == 1
    used, reserved = await _asr_row(url, owner)
    assert used == 1
    assert reserved == 0
    second = await transcribe_audio(
        factory,
        user,
        client_id=client_id,
        mime_type="audio/mp4",
        body=BODY,
        now=NOW,
        provider=provider,
    )
    assert second.text == "你好"
    assert second.replayed is True
    assert second.quotas[0].used == 1
    assert provider.calls == 1
    used, reserved = await _asr_row(url, owner)
    assert used == 1
    assert reserved == 0
    await engine.dispose()


def test_transcribe_empty_invalid_and_timeout_quota() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_edges(url))


async def _assert_edges(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())

    with raises(ApiError) as invalid:
        await transcribe_audio(
            factory,
            user,
            client_id=uuid.uuid4(),
            mime_type="audio/wav",
            body=BODY,
            now=NOW,
            provider=_AsrProvider(),
        )
    assert invalid.value.code == "INVALID_INPUT"
    used, reserved = await _asr_row(url, owner)
    assert used == 0
    assert reserved == 0

    empty_provider = _AsrProvider(Transcript(text="  ", language="zh"))
    with raises(ApiError) as empty:
        await transcribe_audio(
            factory,
            user,
            client_id=uuid.uuid4(),
            mime_type="audio/mp4",
            body=BODY,
            now=NOW,
            provider=empty_provider,
        )
    assert empty.value.code == "ASR_EMPTY_RESULT"
    assert empty.value.status_code == 422
    used, reserved = await _asr_row(url, owner)
    assert used == 1
    assert reserved == 0
    assert empty_provider.calls == 1

    timeout_id = uuid.uuid4()
    timeout_provider = _AsrProvider(error="PROVIDER_TIMEOUT")
    with raises(ApiError) as timed_out:
        await transcribe_audio(
            factory,
            user,
            client_id=timeout_id,
            mime_type="audio/mp4",
            body=BODY,
            now=NOW,
            provider=timeout_provider,
        )
    assert timed_out.value.code == "PROVIDER_TIMEOUT"
    assert timed_out.value.status_code == 504
    used, reserved = await _asr_row(url, owner)
    assert used == 1
    assert reserved == 0
    retry = _AsrProvider()
    recovered = await transcribe_audio(
        factory,
        user,
        client_id=timeout_id,
        mime_type="audio/mp4",
        body=BODY,
        now=NOW,
        provider=retry,
    )
    assert recovered.text == "你好"
    assert recovered.replayed is False
    used, reserved = await _asr_row(url, owner)
    assert used == 2
    assert reserved == 0
    await engine.dispose()
