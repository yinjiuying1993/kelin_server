"""S7-T06: ASR stays in front of chat; TTS quota is written by the chat turn."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.speech import TTS_OUTPUT_MIME, m4a_fixture_bytes
from app.integrations.storage import PrivateTtsStorage
from app.providers.errors import ProviderError
from app.providers.types import ASRInput, AudioResult, ChatInput, ChatOutput, Transcript, TTSInput
from app.schemas.chat import ChatRequest
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import complete_chat_turn
from app.services.speech import transcribe_audio
from app.services.spirit import create_spirit_if_absent
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
SIGNING_KEY = Settings(app_env="test").cursor_signing_key()
REPLY = "我在。"


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid.uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {
                    "document_version": "2026-11-12",
                    "explicitly_accepted": True,
                },
                "data_notice": {"document_version": "2026-11-12", "displayed": True},
                "user_terms": {"document_version": "2026-11-12", "displayed": True},
            },
        }
    )


def _chat(content: str, source: str = "text") -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(uuid.uuid4()),
            "content": content,
            "source": source,
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 21,
                "weather": "cloudy",
                "city": None,
            },
        }
    )


class _Asr:
    source = "stub"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def transcribe(self, value: ASRInput) -> Transcript:
        del value
        self.calls += 1
        return Transcript(text=self.text, language="zh")

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        raise AssertionError("asr must not enter the agent")


class _Voice:
    source = "stub"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.chat_calls = 0
        self.spoken: list[str] = []

    async def chat(self, value: ChatInput) -> ChatOutput:
        del value
        self.chat_calls += 1
        return ChatOutput(reply=REPLY)

    async def synthesize(self, value: TTSInput) -> AudioResult:
        self.spoken.append(value.text)
        if self.fail:
            raise ProviderError("MODEL_UNAVAILABLE")
        return AudioResult(
            body=m4a_fixture_bytes(duration_ms=800),
            mime=TTS_OUTPUT_MIME,
            duration_ms=800,
        )

    async def search(self, value: object) -> list[object]:
        del value
        return []


async def _user(url: str) -> tuple[CurrentUser, object, async_sessionmaker[AsyncSession]]:
    user_id = uuid.uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()
    user = CurrentUser(id=user_id)
    runtime = create_runtime_engine(url)
    factory = create_session_factory(runtime)
    async with claimed_transaction(factory, user) as session:
        await create_spirit_if_absent(session, user, _spirit_request())
    return user, runtime, factory


async def _messages(url: str, user_id: uuid.UUID) -> int:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text(
                "SELECT count(*) FROM public.messages m "
                "JOIN public.spirits s ON s.id = m.spirit_id "
                "WHERE s.user_id = :id"
            ),
            {"id": user_id},
        )
    await engine.dispose()
    return int(value or 0)


async def _tts_used(url: str, user_id: uuid.UUID) -> tuple[int, int]:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT used, reserved FROM public.daily_usage "
                    "WHERE user_id = :id AND capability = 'tts'"
                ),
                {"id": user_id},
            )
        ).first()
    await engine.dispose()
    if row is None:
        return 0, 0
    return int(row.used), int(row.reserved)


def test_cancel_short_and_empty_do_not_enter_agent() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_asr_gate(url))


async def _assert_asr_gate(url: str) -> None:
    user, engine, factory = await _user(url)
    short = _Asr("你好")
    try:
        await transcribe_audio(
            factory,
            user,
            client_id=uuid.uuid4(),
            mime_type="audio/mp4",
            body=m4a_fixture_bytes(duration_ms=400),
            now=NOW,
            provider=short,
        )
    except ApiError as exc:
        assert exc.code == "INVALID_INPUT"
    else:
        raise AssertionError("expected INVALID_INPUT")
    assert short.calls == 0

    cancelled = _Asr("你好")
    try:
        await transcribe_audio(
            factory,
            user,
            client_id=uuid.uuid4(),
            mime_type="audio/mp4",
            body=m4a_fixture_bytes(duration_ms=1200),
            now=NOW,
            provider=cancelled,
            cancelled=True,
        )
    except ApiError as exc:
        assert exc.code == "INVALID_INPUT"
    else:
        raise AssertionError("expected INVALID_INPUT")
    assert cancelled.calls == 0

    blank = _Asr("  ")
    try:
        await transcribe_audio(
            factory,
            user,
            client_id=uuid.uuid4(),
            mime_type="audio/mp4",
            body=m4a_fixture_bytes(duration_ms=1200),
            now=NOW,
            provider=blank,
        )
    except ApiError as exc:
        assert exc.code == "ASR_EMPTY_RESULT"
    else:
        raise AssertionError("expected ASR_EMPTY_RESULT")
    assert blank.calls == 1
    assert await _messages(url, user.id) == 0
    await engine.dispose()


def test_voice_uses_chat_path_and_chat_writes_tts_quota() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_voice(url))


async def _assert_voice(url: str) -> None:
    user, engine, factory = await _user(url)
    provider = _Voice()
    storage = PrivateTtsStorage()
    text_turn = await complete_chat_turn(
        factory, user, _chat("你好"), now=NOW, provider=provider
    )
    voice = await complete_chat_turn(
        factory,
        user,
        _chat("再说一句", "voice"),
        now=NOW,
        provider=provider,
        tts_storage=storage,
        signing_key=SIGNING_KEY,
    )
    assert provider.chat_calls == 2
    assert voice.spirit_content == text_turn.spirit_content == REPLY
    assert provider.spoken == [REPLY]
    assert voice.speech_audio is not None
    used, reserved = await _tts_used(url, user.id)
    assert used == 1
    assert reserved == 0
    await engine.dispose()


def test_tts_failure_and_quota_keep_the_saved_text() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_tts_edges(url))


async def _assert_tts_edges(url: str) -> None:
    user, engine, factory = await _user(url)
    failed = _Voice(fail=True)
    storage = PrivateTtsStorage()
    settled = await complete_chat_turn(
        factory,
        user,
        _chat("你好", "voice"),
        now=NOW,
        provider=failed,
        tts_storage=storage,
        signing_key=SIGNING_KEY,
    )
    assert settled.speech_audio is None
    assert settled.spirit_content == REPLY
    assert await _messages(url, user.id) == 2
    assert await _tts_used(url, user.id) == (0, 0)

    capped_user, capped_engine, capped_factory = await _user(url)
    probe = create_async_engine(url)
    async with probe.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.daily_usage ("
                "user_id, usage_date, timezone, capability, used, reserved, limit_value"
                ") VALUES (:id, DATE '2026-09-09', 'Asia/Shanghai', 'tts', 20, 0, 20)"
            ),
            {"id": capped_user.id},
        )
    await probe.dispose()
    capped = _Voice()
    kept = await complete_chat_turn(
        capped_factory,
        capped_user,
        _chat("你好", "voice"),
        now=NOW,
        provider=capped,
        tts_storage=PrivateTtsStorage(),
        signing_key=SIGNING_KEY,
    )
    assert kept.speech_audio is not None
    assert kept.spirit_content == REPLY
    assert capped.chat_calls == 1
    assert capped.spoken == [REPLY]
    assert await _messages(url, capped_user.id) == 2
    assert await _tts_used(url, capped_user.id) == (21, 0)
    await engine.dispose()
    await capped_engine.dispose()
