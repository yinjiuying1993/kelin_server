from __future__ import annotations

import base64
import json
import random
from collections.abc import Callable
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.core.logging import configure_logging
from app.domain.speech import audio_sha256, m4a_fixture_bytes
from app.integrations.bailian import (
    ASR_GENERATION_URL,
    CHAT_COMPLETIONS_URL,
    CHAT_PROMPT_VERSION,
    EXTRACT_PROMPT_VERSION,
    MCP_AUTO_URL,
    TTS_GENERATION_URL,
    BailianHttpAdapter,
)
from app.providers.errors import ProviderCapabilityUnwired, ProviderError
from app.providers.factory import build_provider
from app.providers.resilience import CapabilityCircuits
from app.providers.stub import ControllableProviderStub
from app.providers.types import (
    ASRInput,
    AudioResult,
    ChatInput,
    ChatOutput,
    ExtractInput,
    ExtractOutput,
    ExtractTurn,
    PromptMemory,
    SafetyInput,
    SearchInput,
    SearchResult,
    Transcript,
    TTSInput,
    VisionInput,
)
from pydantic import SecretStr

SECRET_KEY = "test-only-bailian-key"
SECRET_CONTENT = "UNIQUE_ADAPTER_BODY_DO_NOT_LOG"
TEST_APP_ID = "test-app-id"
_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_ADAPTER = _APP_ROOT / "integrations" / "bailian.py"
_FACTORY = _APP_ROOT / "providers" / "factory.py"
_VENDOR_MARKERS = ("dashscope", "aliyuncs", "compatible-mode")
TransportHandler = Callable[[httpx.Request], httpx.Response]


async def _no_sleep(_seconds: float) -> None:
    return None


def _adapter(
    client: httpx.AsyncClient, *, circuits: CapabilityCircuits | None = None
) -> BailianHttpAdapter:
    return BailianHttpAdapter.from_settings(
        _settings(),
        client=client,
        circuits=circuits if circuits is not None else CapabilityCircuits(),
        sleep=_no_sleep,
        rng=random.Random(0),
    )


def _settings(
    *,
    bailian_app_id: str | None = None,
    bailian_extract_model: str | None = None,
    bailian_search_model: str | None = None,
    bailian_search_enabled: bool = False,
    bailian_vision_model: str | None = None,
    bailian_safety_model: str | None = None,
    bailian_asr_model: str | None = None,
    bailian_tts_model: str | None = None,
    bailian_tts_voice: str | None = None,
) -> Settings:
    return Settings(
        app_env="test",
        bailian_api_key=SecretStr(SECRET_KEY),
        bailian_workspace_id=SecretStr("ws-test"),
        bailian_app_id=bailian_app_id,
        bailian_chat_model="chat",
        bailian_extract_model=bailian_extract_model,
        bailian_search_model=bailian_search_model,
        bailian_search_enabled=bailian_search_enabled,
        bailian_vision_model=bailian_vision_model,
        bailian_safety_model=bailian_safety_model,
        bailian_asr_model=bailian_asr_model,
        bailian_tts_model=bailian_tts_model,
        bailian_tts_voice=bailian_tts_voice,
    )


def _blocked_settings() -> Settings:
    return Settings(
        app_env="test",
        bailian_api_key=None,
        bailian_chat_model=None,
    )


def _input() -> ChatInput:
    return ChatInput(content=SECRET_CONTENT)


def _ok_body() -> dict[str, object]:
    output = ChatOutput(reply="嗯。", intent="companion")
    return {
        "id": "provider-req-1",
        "choices": [{"message": {"content": output.model_dump_json()}}],
    }


def _extract_ok_body() -> dict[str, object]:
    output = ExtractOutput.model_validate({"memories": []})
    return {
        "id": "provider-req-extract",
        "choices": [{"message": {"content": output.model_dump_json()}}],
    }


def _search_ok_body() -> dict[str, object]:
    return {
        "id": "provider-req-search",
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "results": [
                                {"url": "https://example.com/a", "title": "A"},
                            ]
                        }
                    )
                }
            }
        ],
    }


def test_only_integrations_bailian_mentions_vendor_http() -> None:
    vendor_offenders: list[str] = []
    import_offenders: list[str] = []
    for path in _APP_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        if any(marker in lowered for marker in _VENDOR_MARKERS) and path != _ADAPTER:
            vendor_offenders.append(str(path.relative_to(_APP_ROOT)))
        if (
            "from app.integrations.bailian" in text or "import app.integrations.bailian" in text
        ) and path not in {_ADAPTER, _FACTORY}:
            import_offenders.append(str(path.relative_to(_APP_ROOT)))
    assert vendor_offenders == []
    assert import_offenders == []
    adapter = _ADAPTER.read_text(encoding="utf-8")
    assert "dashscope.aliyuncs.com" in adapter
    chat_router = (_APP_ROOT / "api" / "v1" / "chat.py").read_text(encoding="utf-8")
    assert "httpx" not in chat_router
    assert "CHAT_COMPLETIONS_URL" not in chat_router
    extract_router = (_APP_ROOT / "api" / "v1" / "extract.py").read_text(encoding="utf-8")
    assert "httpx" not in extract_router
    assert "dashscope" not in extract_router.lower()
    assert "integrations.bailian" not in extract_router
    service = (_APP_ROOT / "services" / "chat.py").read_text(encoding="utf-8")
    assert "dashscope" not in service.lower()
    assert "httpx" not in service
    assert "CHAT_COMPLETIONS_URL" not in service
    extract_service = (_APP_ROOT / "services" / "extract.py").read_text(encoding="utf-8")
    assert "dashscope" not in extract_service.lower()
    assert "httpx" not in extract_service
    assert "CHAT_COMPLETIONS_URL" not in extract_service
    speech_router = (_APP_ROOT / "api" / "v1" / "speech.py").read_text(encoding="utf-8")
    assert "httpx" not in speech_router
    assert "dashscope" not in speech_router.lower()
    assert "integrations.bailian" not in speech_router
    speech_service = (_APP_ROOT / "services" / "speech.py").read_text(encoding="utf-8")
    assert "dashscope" not in speech_service.lower()
    assert "httpx" not in speech_service
    assert "ASR_GENERATION_URL" not in speech_service
    factory = _FACTORY.read_text(encoding="utf-8")
    assert "CHAT_COMPLETIONS_URL" not in factory
    assert "APP_COMPLETIONS_URL_TEMPLATE" not in factory
    assert "httpx" not in factory


def test_factory_uses_stub_when_secret_missing() -> None:
    adapter = build_provider(_blocked_settings())
    assert isinstance(adapter, ControllableProviderStub)
    assert adapter.source == "stub"


def test_factory_uses_http_adapter_when_chat_ready() -> None:
    adapter = build_provider(_settings())
    assert isinstance(adapter, BailianHttpAdapter)
    assert adapter.source == "provider"


@pytest.mark.asyncio
async def test_adapter_chat_posts_only_to_unique_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = _adapter(client)
        result = await adapter.chat(_input())
    assert result.reply == "嗯。"
    assert result.intent == "companion"
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == CHAT_COMPLETIONS_URL
    assert request.headers["authorization"] == f"Bearer {SECRET_KEY}"
    assert request.headers["x-dashscope-workspace"] == "ws-test"
    payload = json.loads(request.content)
    assert payload["model"] == "chat"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["enable_thinking"] is True
    assert payload["messages"][1]["content"] == SECRET_CONTENT
    assert CHAT_PROMPT_VERSION == "chat/v3"
    assert "刻灵" in payload["messages"][0]["content"]
    assert "spirit_name" in payload["messages"][0]["content"]
    assert "naming: JSON true" in payload["messages"][0]["content"]
    assert "given_name" in payload["messages"][0]["content"]
    assert "just_named" not in payload["messages"][0]["content"]
    assert "intent: exactly the mode value" in payload["messages"][0]["content"]
    assert 'the constant "chat"' not in payload["messages"][0]["content"]
    assert "ordinary companionship turn after hatch" in payload["messages"][0]["content"]
    assert "onboarding step" not in payload["messages"][0]["content"]
    assert "qwen-plus" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_adapter_chat_puts_recall_in_instruction_and_does_not_log_it() -> None:
    seen: list[httpx.Request] = []
    memory_id = uuid4()
    secret_summary = "RECALL_PROMPT_阿年_DO_NOT_LOG"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok_body())

    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = _adapter(client)
            await adapter.chat(
                ChatInput(
                    content=SECRET_CONTENT,
                    memories=[
                        PromptMemory(id=memory_id, type="preference", summary=secret_summary)
                    ],
                )
            )
    payload = json.loads(seen[0].content)
    assert payload["messages"][1]["content"] == SECRET_CONTENT
    assert secret_summary in payload["messages"][0]["content"]
    assert str(memory_id) in payload["messages"][0]["content"]
    dumped = buf.getvalue()
    assert secret_summary not in dumped
    assert SECRET_CONTENT not in dumped
    assert SECRET_KEY not in dumped


@pytest.mark.asyncio
async def test_adapter_chat_uses_completions_when_app_id_set() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_ok_body())

    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = BailianHttpAdapter.from_settings(
                _settings(bailian_app_id=TEST_APP_ID),
                client=client,
                circuits=CapabilityCircuits(),
                sleep=_no_sleep,
                rng=random.Random(0),
            )
            result = await adapter.chat(_input())
    assert result.reply == "嗯。"
    assert result.intent == "companion"
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == CHAT_COMPLETIONS_URL
    assert "apps/" not in str(request.url)
    payload = json.loads(request.content)
    assert payload["model"] == "chat"
    assert payload["messages"][1]["content"] == SECRET_CONTENT
    assert TEST_APP_ID not in json.dumps(payload)
    dumped = buf.getvalue()
    assert SECRET_KEY not in dumped
    assert SECRET_CONTENT not in dumped
    assert TEST_APP_ID not in dumped


@pytest.mark.asyncio
async def test_adapter_extract_stays_on_completions_when_app_id_set() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_extract_ok_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_app_id=TEST_APP_ID, bailian_extract_model="extract"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        await adapter.extract(ExtractInput(conversation_window_id=uuid4()))
    assert str(seen[0].url) == CHAT_COMPLETIONS_URL


@pytest.mark.asyncio
async def test_adapter_maps_timeout_429_5xx_and_rejects_natural_language() -> None:
    async def _call(handler: TransportHandler) -> list[int]:
        seen: list[int] = []

        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(1)
            return handler(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(wrapped)) as client:
            adapter = _adapter(client)
            await adapter.chat(_input())
        return seen

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("upstream")

    with pytest.raises(ProviderError) as timed_out:
        await _call(timeout)
    assert timed_out.value.code == "PROVIDER_TIMEOUT"

    seen_429: list[int] = []

    def too_many(_request: httpx.Request) -> httpx.Response:
        seen_429.append(1)
        return httpx.Response(429, json={"error": {"message": "busy"}})

    with pytest.raises(ProviderError) as limited:
        await _call(too_many)
    assert limited.value.code == "RATE_LIMITED"
    assert len(seen_429) == 2

    seen_5xx: list[int] = []

    def server_error(_request: httpx.Request) -> httpx.Response:
        seen_5xx.append(1)
        return httpx.Response(503, json={"error": {"message": "down"}})

    with pytest.raises(ProviderError) as unavailable:
        await _call(server_error)
    assert unavailable.value.code == "MODEL_UNAVAILABLE"
    assert len(seen_5xx) == 2

    seen_400: list[int] = []

    def client_error(_request: httpx.Request) -> httpx.Response:
        seen_400.append(1)
        return httpx.Response(400, json={"error": {"message": "bad"}})

    with pytest.raises(ProviderError) as bad_request:
        await _call(client_error)
    assert bad_request.value.code == "MODEL_UNAVAILABLE"
    assert len(seen_400) == 1

    def raw_text(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "知道了，阿年。"}}]},
        )

    with pytest.raises(ProviderError) as invalid:
        await _call(raw_text)
    assert invalid.value.code == "CHAT_OUTPUT_INVALID"

    extra = {
        "reply": "嗯。",
        "intent": "companion",
        "citations": [],
        "safety": "allow",
        "unknown": True,
    }

    def extra_fields(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(extra)}}]},
        )

    with pytest.raises(ProviderError) as forbidden:
        await _call(extra_fields)
    assert forbidden.value.code == "CHAT_OUTPUT_INVALID"


@pytest.mark.asyncio
async def test_adapter_logs_omit_secret_and_content() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body())

    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = _adapter(client)
            await adapter.chat(_input())
    dumped = buf.getvalue()
    assert SECRET_KEY not in dumped
    assert SECRET_CONTENT not in dumped
    assert "provider_http" in dumped
    assert '"capability": "chat"' in dumped or '"capability":"chat"' in dumped


@pytest.mark.asyncio
async def test_adapter_safety_and_vision_post_json_without_logging_image() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        payload = json.loads(request.content.decode("utf-8"))
        model = payload["model"]
        if model == "safety":
            content = json.dumps({"decision": "allow"})
        else:
            content = json.dumps({"summary": "窗台上的见闻", "prop": "lamp"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    tiny = b"\xff\xd8\xff"
    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = BailianHttpAdapter.from_settings(
                _settings(bailian_safety_model="safety", bailian_vision_model="vision"),
                client=client,
                circuits=CapabilityCircuits(),
                sleep=_no_sleep,
                rng=random.Random(0),
            )
            safety = await adapter.moderate(SafetyInput(sha256="a" * 64, size_bytes=3, body=tiny))
            vision = await adapter.vision(VisionInput(sha256="a" * 64, size_bytes=3, body=tiny))
    assert safety.decision == "allow"
    assert vision.prop == "lamp"
    assert len(seen) == 2
    dumped = buf.getvalue()
    assert SECRET_KEY not in dumped
    assert "data:image/jpeg;base64" not in dumped


@pytest.mark.asyncio
async def test_live_adapter_unwired_without_extract_or_search_alias() -> None:
    adapter = BailianHttpAdapter.from_settings(_settings())
    with pytest.raises(ProviderCapabilityUnwired) as extract:
        await adapter.extract(ExtractInput(conversation_window_id=uuid4()))
    assert extract.value.capability == "extract"
    with pytest.raises(ProviderCapabilityUnwired) as search:
        await adapter.search(SearchInput(query="q"))
    assert search.value.capability == "search"
    with pytest.raises(ProviderCapabilityUnwired) as transcribe:
        await adapter.transcribe(
            ASRInput(mime_type="audio/mp4", size_bytes=1, duration_ms=1, sha256="a" * 64, body=b"x")
        )
    assert transcribe.value.capability == "transcribe"
    tiny = b"\xff\xd8\xff"
    with pytest.raises(ProviderCapabilityUnwired) as vision:
        await adapter.vision(VisionInput(sha256="a" * 64, size_bytes=3, body=tiny))
    assert vision.value.capability == "vision"
    with pytest.raises(ProviderCapabilityUnwired) as safety:
        await adapter.moderate(SafetyInput(sha256="a" * 64, size_bytes=3, body=tiny))
    assert safety.value.capability == "safety"


@pytest.mark.asyncio
async def test_text_moderation_uses_the_chat_model_when_safety_is_unset() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"decision":"allow"}'}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = _adapter(client)
        decision = await adapter.moderate_text("泡茶")
    assert decision == "allow"
    assert len(seen) == 1
    assert str(seen[0].url) == CHAT_COMPLETIONS_URL
    assert json.loads(seen[0].content)["model"] == "chat"


@pytest.mark.asyncio
async def test_adapter_extract_posts_json_to_unique_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_extract_ok_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_extract_model="extract"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        result = await adapter.extract(
            ExtractInput(
                conversation_window_id=uuid4(),
                turns=[ExtractTurn(role="user", content=SECRET_CONTENT)],
            )
        )
    assert result.memories == []
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == CHAT_COMPLETIONS_URL
    payload = json.loads(request.content)
    assert payload["model"] == "extract"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["enable_thinking"] is False
    assert SECRET_CONTENT in payload["messages"][1]["content"]
    assert EXTRACT_PROMPT_VERSION == "extract/v2"
    prompt = payload["messages"][0]["content"]
    assert "style_samples" not in prompt
    assert "personality_delta" not in prompt
    dumped = json.dumps(payload)
    assert "start_message_id" not in dumped


@pytest.mark.asyncio
async def test_adapter_judge_enables_thinking() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = {
            "id": "provider-req-judge",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"memories": [{"type": "preference", "summary": "喜欢海贼王"}]},
                            ensure_ascii=False,
                        )
                    }
                }
            ],
        }
        return httpx.Response(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_extract_model="extract"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        result = await adapter.judge_memory("喜欢海贼王")
    assert result["memories"] == [{"type": "preference", "summary": "喜欢海贼王"}]
    payload = json.loads(seen[0].content)
    assert payload["enable_thinking"] is True
    assert payload["model"] == "extract"
    assert "喜欢海贼王" in payload["messages"][1]["content"]
    assert "memories" in payload["messages"][0]["content"]


@pytest.mark.asyncio
async def test_adapter_search_does_not_retry_429() -> None:
    seen: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(429, json={"error": {"message": "busy"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_search_model="search", bailian_search_enabled=True),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        with pytest.raises(ProviderError) as limited:
            await adapter.search(SearchInput(query=SECRET_CONTENT))
    assert limited.value.code == "RATE_LIMITED"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_adapter_extract_retries_429_once() -> None:
    seen: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) == 1:
            return httpx.Response(429, json={"error": {"message": "busy"}})
        return httpx.Response(200, json=_extract_ok_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_extract_model="extract"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        result = await adapter.extract(ExtractInput(conversation_window_id=uuid4()))
    assert result.memories == []
    assert result.model_dump().get("style_samples") is None
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_adapter_search_posts_json_and_omits_query_from_logs() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_search_ok_body())

    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = BailianHttpAdapter.from_settings(
                _settings(bailian_search_model="search", bailian_search_enabled=True),
                client=client,
                circuits=CapabilityCircuits(),
                sleep=_no_sleep,
                rng=random.Random(0),
            )
            results = await adapter.search(SearchInput(query=SECRET_CONTENT))
    assert results == [SearchResult(url="https://example.com/a", title="A")]
    assert str(seen[0].url) == CHAT_COMPLETIONS_URL
    payload = json.loads(seen[0].content)
    assert payload["model"] == "search"
    dumped = buf.getvalue()
    assert SECRET_KEY not in dumped
    assert SECRET_CONTENT not in dumped
    assert '"capability": "search"' in dumped or '"capability":"search"' in dumped


@pytest.mark.asyncio
async def test_chat_retries_connect_once_then_succeeds_without_fake_reply() -> None:
    seen: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=_ok_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = _adapter(client)
        result = await adapter.chat(_input())
    assert result.reply == "嗯。"
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_timeout_does_not_retry_or_return_reply() -> None:
    seen: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        seen.append(1)
        raise httpx.TimeoutException("upstream")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = _adapter(client)
        with pytest.raises(ProviderError) as timed_out:
            await adapter.chat(_input())
    assert timed_out.value.code == "PROVIDER_TIMEOUT"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_invalid_chat_json_does_not_open_the_circuit() -> None:
    seen: list[int] = []
    circuits = CapabilityCircuits()

    def handler(_request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "知道了，阿年。"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = _adapter(client, circuits=circuits)
        with pytest.raises(ProviderError) as first:
            await adapter.chat(_input())
        with pytest.raises(ProviderError) as second:
            await adapter.chat(_input())
    assert first.value.code == "CHAT_OUTPUT_INVALID"
    assert second.value.code == "CHAT_OUTPUT_INVALID"
    assert circuits.is_open("chat") is False
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_chat_circuit_opens_independently_and_blocks_without_http() -> None:
    seen: list[int] = []
    clock = {"t": 0.0}
    circuits = CapabilityCircuits(clock=lambda: clock["t"])

    def handler(_request: httpx.Request) -> httpx.Response:
        seen.append(1)
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = _adapter(client, circuits=circuits)
        with pytest.raises(ProviderError):
            await adapter.chat(_input())
        assert len(seen) == 2
        assert circuits.is_open("chat")
        assert circuits.is_open("search") is False
        with pytest.raises(ProviderError) as blocked:
            await adapter.chat(_input())
        assert blocked.value.code == "MODEL_UNAVAILABLE"
        assert len(seen) == 2
        clock["t"] = 120
        seen.clear()

        def recover(_request: httpx.Request) -> httpx.Response:
            seen.append(1)
            return httpx.Response(200, json=_ok_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(recover)) as client:
        adapter = _adapter(client, circuits=circuits)
        result = await adapter.chat(_input())
    assert result.reply == "嗯。"
    assert len(seen) == 1
    assert circuits.is_open("chat") is False


def _asr_input() -> ASRInput:
    body = m4a_fixture_bytes(duration_ms=800)
    return ASRInput(
        mime_type="audio/mp4",
        size_bytes=len(body),
        duration_ms=800,
        sha256=audio_sha256(body),
        body=body,
    )


def _asr_ok_body(*, text: str = "你好") -> dict[str, object]:
    return {
        "request_id": "asr-req-1",
        "output": {"text": text, "language": "zh"},
    }


@pytest.mark.asyncio
async def test_adapter_asr_posts_to_generation_url_without_logging_audio() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_asr_ok_body())

    payload = _asr_input()
    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = BailianHttpAdapter.from_settings(
                _settings(bailian_asr_model="asr"),
                client=client,
                circuits=CapabilityCircuits(),
                sleep=_no_sleep,
                rng=random.Random(0),
            )
            result = await adapter.transcribe(payload)
    assert result == Transcript(text="你好", language="zh", provider_request_id="asr-req-1")
    assert len(seen) == 1
    assert str(seen[0].url) == ASR_GENERATION_URL
    body = json.loads(seen[0].content)
    assert body["model"] == "asr"
    assert "qwen-" not in json.dumps(body)
    item = body["input"]["messages"][0]["content"][0]
    assert item["type"] == "input_audio"
    assert item["input_audio"]["data"].startswith("data:audio/mp4;base64,")
    assert body["parameters"]["format"] == "m4a"
    dumped = buf.getvalue()
    assert SECRET_KEY not in dumped
    assert "data:audio/mp4;base64" not in dumped
    assert payload.body not in dumped.encode("utf-8", errors="ignore")
    assert '"capability": "asr"' in dumped or '"capability":"asr"' in dumped


@pytest.mark.asyncio
async def test_adapter_asr_empty_timeout_429_and_unwired() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(200, json=_asr_ok_body(text="  "))
        )
    ) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_asr_model="asr"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        empty = await adapter.transcribe(_asr_input())
    assert empty.text == ""

    seen_timeout: list[int] = []

    def timeout(_request: httpx.Request) -> httpx.Response:
        seen_timeout.append(1)
        raise httpx.TimeoutException("upstream")

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_asr_model="asr"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        with pytest.raises(ProviderError) as timed_out:
            await adapter.transcribe(_asr_input())
    assert timed_out.value.code == "PROVIDER_TIMEOUT"
    assert len(seen_timeout) == 1

    seen_429: list[int] = []

    def busy_then_ok(_request: httpx.Request) -> httpx.Response:
        seen_429.append(1)
        if len(seen_429) == 1:
            return httpx.Response(429, json={"error": {"message": "busy"}})
        return httpx.Response(200, json=_asr_ok_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(busy_then_ok)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_asr_model="asr"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        result = await adapter.transcribe(_asr_input())
    assert result.text == "你好"
    assert len(seen_429) == 2

    seen_empty: list[int] = []

    def no_words(_request: httpx.Request) -> httpx.Response:
        seen_empty.append(1)
        return httpx.Response(
            400,
            json={"code": "ASR_RESPONSE_HAVE_NO_WORDS", "message": "ASR_RESPONSE_HAVE_NO_WORDS"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(no_words)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_asr_model="asr"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        silent = await adapter.transcribe(_asr_input())
    assert silent.text == ""
    assert len(seen_empty) == 1

    adapter = BailianHttpAdapter.from_settings(_settings())
    with pytest.raises(ProviderCapabilityUnwired) as unwired:
        await adapter.transcribe(_asr_input())
    assert unwired.value.capability == "transcribe"


def _tts_input() -> TTSInput:
    return TTSInput(text=SECRET_CONTENT, voice_profile="default", message_id=uuid4())


def _tts_ok_body() -> dict[str, object]:
    encoded = base64.b64encode(m4a_fixture_bytes(duration_ms=800)).decode("ascii")
    return {"request_id": "tts-req-1", "output": {"audio": encoded}}


@pytest.mark.asyncio
async def test_adapter_tts_posts_to_generation_url_without_logging_text() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_tts_ok_body())

    payload = _tts_input()
    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = BailianHttpAdapter.from_settings(
                _settings(bailian_tts_model="tts"),
                client=client,
                circuits=CapabilityCircuits(),
                sleep=_no_sleep,
                rng=random.Random(0),
            )
            result = await adapter.synthesize(payload)
    assert isinstance(result, AudioResult)
    assert result.mime == "audio/mp4"
    assert result.duration_ms == 800
    assert result.provider_request_id == "tts-req-1"
    assert len(seen) == 1
    assert str(seen[0].url) == TTS_GENERATION_URL
    body = json.loads(seen[0].content)
    assert body["model"] == "tts"
    assert body["input"]["voice"] == "default"
    assert body["input"]["format"] == "wav"
    assert "qwen-" not in json.dumps(body)
    dumped = buf.getvalue()
    assert SECRET_KEY not in dumped
    assert SECRET_CONTENT not in dumped
    assert "data:audio/mp4;base64" not in dumped
    assert '"capability": "tts"' in dumped or '"capability":"tts"' in dumped


@pytest.mark.asyncio
async def test_adapter_tts_timeout_429_and_unwired() -> None:
    seen_timeout: list[int] = []

    def timeout(_request: httpx.Request) -> httpx.Response:
        seen_timeout.append(1)
        raise httpx.TimeoutException("upstream")

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_tts_model="tts"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        with pytest.raises(ProviderError) as timed_out:
            await adapter.synthesize(_tts_input())
    assert timed_out.value.code == "PROVIDER_TIMEOUT"
    assert len(seen_timeout) == 1

    seen_429: list[int] = []

    def busy_then_ok(_request: httpx.Request) -> httpx.Response:
        seen_429.append(1)
        if len(seen_429) == 1:
            return httpx.Response(429, json={"error": {"message": "busy"}})
        return httpx.Response(200, json=_tts_ok_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(busy_then_ok)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(bailian_tts_model="tts"),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        result = await adapter.synthesize(_tts_input())
    assert result.duration_ms == 800
    assert len(seen_429) == 2

    adapter = BailianHttpAdapter.from_settings(_settings())
    with pytest.raises(ProviderCapabilityUnwired) as unwired:
        await adapter.synthesize(_tts_input())
    assert unwired.value.capability == "synthesize"


@pytest.mark.asyncio
async def test_adapter_tts_maps_voice_from_settings_without_logging_it() -> None:
    seen: list[httpx.Request] = []
    vendor_voice = "vendor-voice-xyz"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_tts_ok_body())

    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = BailianHttpAdapter.from_settings(
                _settings(bailian_tts_model="tts", bailian_tts_voice=vendor_voice),
                client=client,
                circuits=CapabilityCircuits(),
                sleep=_no_sleep,
                rng=random.Random(0),
            )
            await adapter.synthesize(_tts_input())
    assert json.loads(seen[0].content)["input"]["voice"] == vendor_voice
    dumped = buf.getvalue()
    assert vendor_voice not in dumped
    assert SECRET_CONTENT not in dumped


@pytest.mark.asyncio
async def test_adapter_tts_downloads_audio_url_without_auth_or_logging() -> None:
    seen: list[httpx.Request] = []
    audio_url = "https://example.invalid/tts/signed-object"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "GET":
            assert "authorization" not in {key.lower() for key in request.headers}
            return httpx.Response(
                200,
                content=m4a_fixture_bytes(duration_ms=800),
                headers={"content-type": "audio/wav"},
            )
        return httpx.Response(
            200,
            json={"request_id": "tts-req-url", "output": {"audio": {"url": audio_url}}},
        )

    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = BailianHttpAdapter.from_settings(
                _settings(bailian_tts_model="tts"),
                client=client,
                circuits=CapabilityCircuits(),
                sleep=_no_sleep,
                rng=random.Random(0),
            )
            result = await adapter.synthesize(_tts_input())
    assert result.duration_ms == 800
    assert result.provider_request_id == "tts-req-url"
    assert [request.method for request in seen] == ["POST", "GET"]
    assert str(seen[1].url) == audio_url
    dumped = buf.getvalue()
    assert audio_url not in dumped
    assert "signed-object" not in dumped
    assert SECRET_CONTENT not in dumped


@pytest.mark.asyncio
async def test_adapter_tts_remuxes_non_m4a_without_logging_bytes() -> None:
    wav_body = b"RIFF" + b"\x00" * 12

    def handler(_request: httpx.Request) -> httpx.Response:
        encoded = base64.b64encode(wav_body).decode("ascii")
        return httpx.Response(200, json={"request_id": "tts-req-wav", "output": {"audio": encoded}})

    buf = StringIO()
    with (
        patch("sys.stdout", buf),
        patch(
            "app.integrations.bailian._ffmpeg_to_m4a",
            return_value=m4a_fixture_bytes(duration_ms=800),
        ) as remux,
    ):
        configure_logging(Settings(app_env="test"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = BailianHttpAdapter.from_settings(
                _settings(bailian_tts_model="tts"),
                client=client,
                circuits=CapabilityCircuits(),
                sleep=_no_sleep,
                rng=random.Random(0),
            )
            result = await adapter.synthesize(_tts_input())
    remux.assert_called_once_with(wav_body)
    assert result.duration_ms == 800
    dumped = buf.getvalue()
    assert SECRET_CONTENT not in dumped
    assert "RIFF" not in dumped


def _mcp_tool_body(text: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"content": [{"type": "text", "text": text}]},
    }


@pytest.mark.asyncio
async def test_adapter_extend_calls_bailian_auto_mcp() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if not request.content:
            return httpx.Response(202)
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "sess-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "serverInfo": {"name": "mcp", "version": "1"},
                    },
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/call":
            name = payload["params"]["name"]
            if name == "search_tool":
                found = json.dumps(
                    {
                        "search_data": [
                            {
                                "server_code": "amap-maps",
                                "tool_name": "maps_weather",
                                "tool_inputSchema": {
                                    "type": "object",
                                    "properties": {"city": {"type": "string"}},
                                    "required": ["city"],
                                },
                            }
                        ]
                    }
                )
                return httpx.Response(200, json=_mcp_tool_body(found))
            arguments = payload["params"]["arguments"]
            assert arguments["server_code"] == "amap-maps"
            assert arguments["tool_name"] == "maps_weather"
            assert arguments["params"] == {"city": "杭州"}
            return httpx.Response(200, json=_mcp_tool_body("杭州 晴 18℃"))
        return httpx.Response(500, json={"error": "unexpected"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        text = await adapter.extend(query="查询城市天气", argument="杭州")
    assert text == "杭州 晴 18℃"
    assert {str(item.url) for item in seen} == {MCP_AUTO_URL}


@pytest.mark.asyncio
async def test_adapter_extend_keeps_the_leading_hot_news() -> None:
    blob = json.dumps(
        {
            "num_23_hot_news_title": "靠后的一条",
            "num_23_hot_news_url": "https://news.example/23",
            "num_1_hot_news_title": "今日第一条热点",
            "num_1_hot_news_url": "https://news.example/1",
            "num_2_hot_news_title": "今日第二条热点",
            "num_2_hot_news_url": "https://news.example/2",
        },
        ensure_ascii=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        name = payload["params"]["name"]
        if name == "search_tool":
            found = json.dumps(
                {
                    "search_data": [
                        {
                            "server_code": "news",
                            "tool_name": "news.hotnews",
                            "tool_inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                }
            )
            return httpx.Response(200, json=_mcp_tool_body(found))
        return httpx.Response(200, json=_mcp_tool_body(blob))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        text = await adapter.extend(query="今天热点新闻")
    assert text.splitlines()[0].startswith("今日第一条热点")
    assert "https://news.example/1" in text
    assert "靠后的一条 https://news.example/23" in text


@pytest.mark.asyncio
async def test_adapter_extend_skips_a_closed_news_tool() -> None:
    called: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.content:
            return httpx.Response(202)
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        name = payload["params"]["name"]
        if name == "search_tool":
            found = json.dumps(
                {
                    "search_data": [
                        {
                            "server_code": "finance",
                            "tool_name": "SearchFinancialNews",
                            "tool_inputSchema": {"type": "object", "properties": {}},
                        },
                        {
                            "server_code": "news",
                            "tool_name": "news.hotnews",
                            "tool_inputSchema": {"type": "object", "properties": {}},
                        },
                    ]
                }
            )
            return httpx.Response(200, json=_mcp_tool_body(found))
        tool_name = payload["params"]["arguments"]["tool_name"]
        called.append(tool_name)
        if tool_name == "SearchFinancialNews":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "MCP服务开通中，请稍等"}],
                    },
                },
            )
        return httpx.Response(
            200,
            json=_mcp_tool_body(
                json.dumps(
                    {
                        "num_1_hot_news_title": "今日第一条热点",
                        "num_1_hot_news_url": "https://news.example/1",
                    },
                    ensure_ascii=False,
                )
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        text = await adapter.extend(query="检索新闻", argument="今天有什么新闻")
    assert text.startswith("今日第一条热点")
    assert called == ["news.hotnews"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_text", "channel"),
    [("今天有什么大瓜", "娱乐最新"), ("今天的新闻热点", "国内最新")],
)
async def test_adapter_extend_picks_a_news_channel(user_text: str, channel: str) -> None:
    seen: list[dict[str, str]] = []
    searched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.content:
            return httpx.Response(202)
        payload = json.loads(request.content)
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        name = payload["params"]["name"]
        if name == "search_tool":
            searched.append(payload["params"]["arguments"]["query"])
            found = json.dumps(
                {
                    "search_data": [
                        {
                            "server_code": "news",
                            "tool_name": "news.hotnews",
                            "tool_inputSchema": {"type": "object", "properties": {}},
                        },
                        {
                            "server_code": "news",
                            "tool_name": "news.query",
                            "tool_inputSchema": {
                                "type": "object",
                                "properties": {"channel_name": {"type": "string"}},
                            },
                        },
                    ]
                }
            )
            return httpx.Response(200, json=_mcp_tool_body(found))
        arguments = payload["params"]["arguments"]
        seen.append(arguments["params"])
        return httpx.Response(
            200,
            json=_mcp_tool_body(
                json.dumps(
                    {
                        "num_1_news_title": "第一条",
                        "num_1_news_url": "https://news.example/1",
                    },
                    ensure_ascii=False,
                )
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = BailianHttpAdapter.from_settings(
            _settings(),
            client=client,
            circuits=CapabilityCircuits(),
            sleep=_no_sleep,
            rng=random.Random(0),
        )
        text = await adapter.extend(query="今日热点新闻", user_text=user_text)
    assert text == "第一条 https://news.example/1"
    assert seen == [{"channel_name": channel}]
    assert searched == ["娱乐新闻" if channel == "娱乐最新" else "时事热点新闻"]
