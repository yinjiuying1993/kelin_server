"""Unique Bailian HTTP adapter. Spec §§16.1, 16.5.

Router, Service, Repository, and worker handlers must not import this module's
URL or send DashScope requests themselves.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import random
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Literal, TypeVar, cast
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.speech import TTS_OUTPUT_MIME, InvalidSpeech, tts_audio_duration_ms
from app.providers.contract import PROVIDER_TIMEOUT_SECONDS, map_provider_failure
from app.providers.errors import ProviderCapabilityUnwired, ProviderError
from app.providers.resilience import (
    CapabilityCircuits,
    default_circuits,
    retry_delay_seconds,
    should_retry,
)
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

CHAT_COMPLETIONS_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
APP_COMPLETIONS_URL_TEMPLATE = "https://dashscope.aliyuncs.com/api/v1/apps/{app_id}/completion"
ASR_GENERATION_URL = (
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
)
TTS_GENERATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
_TTS_REMUX_TIMEOUT_SECONDS = 15
_TTS_DOWNLOAD_SCHEMES = frozenset({"http", "https"})
AGENT_APP_ALIAS = "agent_app"
PROVIDER_SOURCE: Literal["provider"] = "provider"
CHAT_PROMPT_VERSION = "chat/v3"
EXTRACT_PROMPT_VERSION = "extract/v2"
SAFETY_PROMPT_VERSION = "safety/v1"
VISION_PROMPT_VERSION = "vision/v1"
JsonCapability = Literal["chat", "extract", "search", "vision", "safety", "asr", "tts"]
_PROMPTS_ROOT = Path(__file__).resolve().parent / "bailian" / "prompts"
_SEARCH_INSTRUCTION = (
    "Return only JSON with key results. results is a list of at most 3 objects "
    "with url and optional title. url must be https. No extra keys."
)
Sleep = Callable[[float], Awaitable[None]]
T = TypeVar("T")


class _AttemptFailure(Exception):
    def __init__(self, kind: str, error: ProviderError) -> None:
        self.kind = kind
        self.error = error
        super().__init__(kind)


class _SearchEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[SearchResult] = Field(default_factory=list, max_length=3)


@dataclass(frozen=True, slots=True)
class _TtsWire:
    body: bytes | None
    audio_url: str | None
    provider_request_id: str | None


class BailianHttpAdapter:
    def __init__(
        self,
        *,
        api_key: SecretStr,
        chat_model: str,
        app_id: str | None,
        extract_model: str | None,
        search_model: str | None,
        vision_model: str | None,
        safety_model: str | None,
        asr_model: str | None,
        tts_model: str | None,
        tts_voice: str | None,
        search_enabled: bool,
        workspace_id: SecretStr | None,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
        circuits: CapabilityCircuits | None = None,
        sleep: Sleep | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._api_key = api_key
        self._chat_model = chat_model
        self._app_id = app_id
        self._extract_model = extract_model
        self._search_model = search_model
        self._vision_model = vision_model
        self._safety_model = safety_model
        self._asr_model = asr_model
        self._tts_model = tts_model
        self._tts_voice = tts_voice
        self._search_enabled = search_enabled
        self._workspace_id = workspace_id
        self._timeout_seconds = timeout_seconds
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = client
        self._circuits = circuits if circuits is not None else default_circuits()
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._rng = rng if rng is not None else random.Random()
        self._logger = get_logger(component="bailian")

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        circuits: CapabilityCircuits | None = None,
        sleep: Sleep | None = None,
        rng: random.Random | None = None,
    ) -> BailianHttpAdapter:
        if settings.bailian_api_key is None or settings.bailian_chat_model is None:
            raise ProviderError("MODEL_UNAVAILABLE")
        return cls(
            api_key=settings.bailian_api_key,
            chat_model=settings.bailian_chat_model,
            app_id=settings.bailian_app_id,
            extract_model=settings.bailian_extract_model,
            search_model=settings.bailian_search_model,
            vision_model=settings.bailian_vision_model,
            safety_model=settings.bailian_safety_model,
            asr_model=settings.bailian_asr_model,
            tts_model=settings.bailian_tts_model,
            tts_voice=settings.bailian_tts_voice,
            search_enabled=settings.bailian_search_enabled,
            workspace_id=settings.bailian_workspace_id,
            timeout_seconds=float(PROVIDER_TIMEOUT_SECONDS["chat"]),
            client=client,
            circuits=circuits,
            sleep=sleep,
            rng=rng,
        )

    @property
    def source(self) -> Literal["provider"]:
        return PROVIDER_SOURCE

    async def chat(self, value: ChatInput) -> ChatOutput:
        if self._app_id is not None:
            return await self._complete_json(
                capability="chat",
                model=AGENT_APP_ALIAS,
                url=app_completions_url(self._app_id),
                payload={
                    "input": {"prompt": _agent_chat_prompt(value)},
                    "parameters": {},
                    "debug": {},
                },
                parse=_chat_output_from_agent_response,
            )
        return await self._complete_json(
            capability="chat",
            model=self._chat_model,
            url=CHAT_COMPLETIONS_URL,
            payload=_completions_payload(
                _chat_instruction(value),
                value.content,
                self._chat_model,
            ),
            parse=_chat_output_from_response,
        )

    async def extract(self, value: ExtractInput) -> ExtractOutput:
        if self._extract_model is None:
            raise ProviderCapabilityUnwired("extract")
        return await self._complete_json(
            capability="extract",
            model=self._extract_model,
            url=CHAT_COMPLETIONS_URL,
            payload=_completions_payload(
                _load_prompt(EXTRACT_PROMPT_VERSION),
                _extract_transcript(value),
                self._extract_model,
            ),
            parse=_extract_output_from_response,
        )

    async def search(self, value: SearchInput) -> list[SearchResult]:
        if not self._search_enabled or self._search_model is None:
            raise ProviderCapabilityUnwired("search")
        return await self._complete_json(
            capability="search",
            model=self._search_model,
            url=CHAT_COMPLETIONS_URL,
            payload=_completions_payload(
                _SEARCH_INSTRUCTION,
                value.query,
                self._search_model,
            ),
            parse=_search_results_from_response,
        )

    async def transcribe(self, value: ASRInput) -> Transcript:
        if self._asr_model is None:
            raise ProviderCapabilityUnwired("transcribe")
        if not isinstance(value, ASRInput):
            raise ProviderError("MODEL_UNAVAILABLE")
        return await self._complete_json(
            capability="asr",
            model=self._asr_model,
            url=ASR_GENERATION_URL,
            payload=_asr_payload(value, self._asr_model),
            parse=_transcript_from_response,
        )

    async def synthesize(self, value: TTSInput) -> AudioResult:
        if self._tts_model is None:
            raise ProviderCapabilityUnwired("synthesize")
        if not isinstance(value, TTSInput):
            raise ProviderError("MODEL_UNAVAILABLE")
        wire = await self._complete_json(
            capability="tts",
            model=self._tts_model,
            url=TTS_GENERATION_URL,
            payload=_tts_payload(value, self._tts_model, self._tts_voice),
            parse=_tts_wire_from_response,
        )
        body = await self._tts_audio_body(wire)
        try:
            playback = _ensure_playback_m4a(body)
            duration_ms = tts_audio_duration_ms(playback)
        except InvalidSpeech as exc:
            raise ProviderError("MODEL_UNAVAILABLE") from exc
        return AudioResult(
            body=playback,
            mime=TTS_OUTPUT_MIME,
            duration_ms=duration_ms,
            provider_request_id=wire.provider_request_id,
        )

    async def _tts_audio_body(self, wire: _TtsWire) -> bytes:
        if wire.body is not None:
            return wire.body
        if wire.audio_url is None:
            raise ProviderError("MODEL_UNAVAILABLE")
        parsed = urlparse(wire.audio_url)
        if parsed.scheme not in _TTS_DOWNLOAD_SCHEMES or not parsed.netloc:
            raise ProviderError("MODEL_UNAVAILABLE")
        try:
            async with self._http_client() as client:
                response = await client.get(
                    wire.audio_url,
                    timeout=float(PROVIDER_TIMEOUT_SECONDS["tts"]),
                )
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise ProviderError("MODEL_UNAVAILABLE") from exc
        if response.status_code >= 400 or not response.content:
            raise ProviderError("MODEL_UNAVAILABLE")
        return bytes(response.content)

    async def vision(self, value: VisionInput) -> VisionOutput:
        if self._vision_model is None:
            raise ProviderCapabilityUnwired("vision")
        if not isinstance(value, VisionInput):
            raise ProviderError("MODEL_UNAVAILABLE")
        return await self._complete_json(
            capability="vision",
            model=self._vision_model,
            url=CHAT_COMPLETIONS_URL,
            payload=_image_payload(value.body, self._vision_model, VISION_PROMPT_VERSION),
            parse=_vision_output_from_response,
        )

    async def moderate(self, value: SafetyInput) -> SafetyOutput:
        if self._safety_model is None:
            raise ProviderCapabilityUnwired("safety")
        if not isinstance(value, SafetyInput):
            raise ProviderError("MODEL_UNAVAILABLE")
        return await self._complete_json(
            capability="safety",
            model=self._safety_model,
            url=CHAT_COMPLETIONS_URL,
            payload=_image_payload(value.body, self._safety_model, SAFETY_PROMPT_VERSION),
            parse=_safety_output_from_response,
        )

    async def _complete_json(
        self,
        *,
        capability: JsonCapability,
        model: str,
        url: str,
        payload: Mapping[str, object],
        parse: Callable[[httpx.Response], T],
    ) -> T:
        if not self._circuits.allow(capability):
            self._log_call(
                capability=capability,
                model_alias=model,
                outcome="circuit_open",
                started=perf_counter(),
                status_code=None,
            )
            raise ProviderError("MODEL_UNAVAILABLE")
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        if self._workspace_id is not None:
            headers["X-DashScope-WorkSpace"] = self._workspace_id.get_secret_value()
        deadline_at = perf_counter() + float(PROVIDER_TIMEOUT_SECONDS[capability])
        retries_used = 0
        last_error: ProviderError | None = None
        while True:
            remaining = deadline_at - perf_counter()
            if remaining <= 0:
                mapped = map_provider_failure("timeout")
                raise last_error if last_error is not None else ProviderError(mapped.code)
            started = perf_counter()
            try:
                output = await self._complete_once(
                    capability=capability,
                    model_alias=model,
                    url=url,
                    headers=headers,
                    payload=payload,
                    remaining=remaining,
                    started=started,
                    parse=parse,
                )
            except _AttemptFailure as exc:
                self._circuits.record(capability, ok=False)
                last_error = exc.error
                if should_retry(
                    exc.kind,
                    capability=capability,
                    retries_used=retries_used,
                    remaining_seconds=deadline_at - perf_counter(),
                    rng=self._rng,
                ):
                    retries_used += 1
                    await self._sleep(retry_delay_seconds(retries_used, rng=self._rng))
                    if not self._circuits.allow(capability):
                        self._log_call(
                            capability=capability,
                            model_alias=model,
                            outcome="circuit_open",
                            started=started,
                            status_code=None,
                        )
                        raise last_error
                    continue
                raise last_error from exc
            self._circuits.record(capability, ok=True)
            return output

    async def _complete_once(
        self,
        *,
        capability: JsonCapability,
        model_alias: str,
        url: str,
        headers: dict[str, str],
        payload: Mapping[str, object],
        remaining: float,
        started: float,
        parse: Callable[[httpx.Response], T],
    ) -> T:
        try:
            async with self._http_client() as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=dict(payload),
                    timeout=remaining,
                )
        except httpx.TimeoutException as exc:
            self._log_call(
                capability=capability,
                model_alias=model_alias,
                outcome="timeout",
                started=started,
                status_code=None,
            )
            mapped = map_provider_failure("timeout")
            raise _AttemptFailure("timeout", ProviderError(mapped.code)) from exc
        except httpx.ConnectError as exc:
            self._log_call(
                capability=capability,
                model_alias=model_alias,
                outcome="connect",
                started=started,
                status_code=None,
            )
            mapped = map_provider_failure("connect")
            raise _AttemptFailure("connect", ProviderError(mapped.code)) from exc
        self._log_call(
            capability=capability,
            model_alias=model_alias,
            outcome="http",
            started=started,
            status_code=response.status_code,
            provider_request_id=_response_id(response),
        )
        if response.status_code == 429:
            mapped = map_provider_failure("http_429")
            raise _AttemptFailure("http_429", ProviderError(mapped.code))
        if response.status_code >= 500:
            mapped = map_provider_failure("http_5xx")
            raise _AttemptFailure("http_5xx", ProviderError(mapped.code))
        if response.status_code >= 400:
            empty = _empty_asr_from_http(capability, response)
            if empty is not None:
                return cast(T, empty)
            raise _AttemptFailure("http_4xx", ProviderError("MODEL_UNAVAILABLE"))
        try:
            return parse(response)
        except ProviderError as exc:
            raise _AttemptFailure("schema", exc) from exc

    @asynccontextmanager
    async def _http_client(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            yield client

    def _log_call(
        self,
        *,
        capability: JsonCapability,
        model_alias: str,
        outcome: str,
        started: float,
        status_code: int | None,
        provider_request_id: str | None = None,
    ) -> None:
        self._logger.info(
            "provider_http",
            source=PROVIDER_SOURCE,
            capability=capability,
            model_alias=model_alias,
            outcome=outcome,
            status_code=status_code,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            provider_request_id=provider_request_id,
        )


def app_completions_url(app_id: str) -> str:
    return APP_COMPLETIONS_URL_TEMPLATE.format(app_id=app_id)


def _completions_payload(instruction: str, user_content: str, model: str) -> dict[str, object]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }


def _load_prompt(version: str) -> str:
    relative = Path(*version.split("/")).with_suffix(".txt")
    path = _PROMPTS_ROOT / relative
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if not text:
        raise ProviderError("MODEL_UNAVAILABLE")
    return text


def _recall_block(value: ChatInput) -> str:
    if not value.memories and not value.style_samples:
        return ""
    lines = [
        "Active private memories only. Cite only ids listed here; omit sealed or deleted.",
    ]
    for memory in value.memories:
        lines.append(f"- {memory.id} [{memory.type}] {memory.summary}")
    if value.style_samples:
        lines.append("Active style samples:")
        for sample in value.style_samples:
            lines.append(f"- {sample.kind}: {sample.text}")
    return "\n".join(lines)


def _agent_chat_prompt(value: ChatInput) -> str:
    if value.onboarding:
        base = (
            f"{value.content}\n"
            f"(S04 onboarding step {value.onboarding_step} of 5; "
            "keep reply within 20 Chinese characters.)"
        )
    else:
        base = value.content
    block = _recall_block(value)
    if not block:
        return base
    return f"{base}\n{block}"


def _chat_instruction(value: ChatInput) -> str:
    if value.onboarding:
        phase = (
            f"This is S04 onboarding step {value.onboarding_step} of 5. "
            "Keep reply within 20 Chinese characters."
        )
    else:
        phase = "This is an ordinary companionship turn after hatch."
    instruction = f"{_load_prompt(CHAT_PROMPT_VERSION)}\n{phase}"
    block = _recall_block(value)
    if not block:
        return instruction
    return f"{instruction}\n{block}"


def _extract_transcript(value: ExtractInput) -> str:
    lines = [f"{turn.role}: {turn.content}" for turn in value.turns]
    return "\n".join(lines) if lines else "(empty)"


def _response_id(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        for key in ("request_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _message_json_object(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if not isinstance(payload, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("MODEL_UNAVAILABLE")
    first = choices[0]
    if not isinstance(first, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("MODEL_UNAVAILABLE")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if not isinstance(parsed, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    return parsed


_CHAT_OUTPUT_KEYS = ("reply", "intent", "citations", "safety", "search_query")


def _agent_output_text(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if not isinstance(payload, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    output = payload.get("output")
    if not isinstance(output, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    text = output.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("MODEL_UNAVAILABLE")
    return text.strip()


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines or not lines[0].startswith("```"):
        return stripped
    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _json_object_from_agent_text(text: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(_strip_markdown_fence(text))
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _chat_output_from_whitelist(parsed: dict[str, object]) -> ChatOutput | None:
    subset = {key: parsed[key] for key in _CHAT_OUTPUT_KEYS if key in parsed}
    try:
        return ChatOutput.model_validate(subset)
    except ValidationError:
        return None


def _chat_output_from_agent_response(response: httpx.Response) -> ChatOutput:
    text = _agent_output_text(response)
    parsed = _json_object_from_agent_text(text)
    if parsed is not None:
        output = _chat_output_from_whitelist(parsed)
        if output is not None:
            return output
        if "reply" in parsed:
            raise ProviderError("MODEL_UNAVAILABLE")
    reply = text[:4000]
    if not reply:
        raise ProviderError("MODEL_UNAVAILABLE")
    return ChatOutput(reply=reply)


def _chat_output_from_response(response: httpx.Response) -> ChatOutput:
    try:
        return ChatOutput.model_validate(_message_json_object(response))
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc


def _extract_output_from_response(response: httpx.Response) -> ExtractOutput:
    try:
        return ExtractOutput.model_validate(_message_json_object(response))
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc


def _search_results_from_response(response: httpx.Response) -> list[SearchResult]:
    try:
        envelope = _SearchEnvelope.model_validate(_message_json_object(response))
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    return list(envelope.results)


def _empty_asr_from_http(capability: JsonCapability, response: httpx.Response) -> Transcript | None:
    if capability != "asr":
        return None
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    message = payload.get("message")
    code_text = code if isinstance(code, str) else ""
    message_text = message if isinstance(message, str) else ""
    if code_text != "ASR_RESPONSE_HAVE_NO_WORDS" and "HAVE_NO_WORDS" not in message_text:
        return None
    return Transcript(text="", language="zh", provider_request_id=_response_id(response))


def _asr_payload(value: ASRInput, model: str) -> dict[str, object]:
    encoded = base64.b64encode(value.body).decode("ascii")
    return {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": f"data:audio/mp4;base64,{encoded}"},
                        }
                    ],
                }
            ]
        },
        "parameters": {"format": "m4a"},
    }


def _tts_payload(value: TTSInput, model: str, vendor_voice: str | None) -> dict[str, object]:
    return {
        "model": model,
        "input": {
            "text": value.text,
            "voice": vendor_voice if vendor_voice else value.voice_profile,
            "format": "wav",
        },
    }


def _tts_wire_from_response(response: httpx.Response) -> _TtsWire:
    request_id = _response_id(response)
    content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("audio/"):
        body = bytes(response.content)
        if not body:
            raise ProviderError("MODEL_UNAVAILABLE")
        return _TtsWire(body=body, audio_url=None, provider_request_id=request_id)
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if not isinstance(payload, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    decoded, audio_url = _tts_audio_ref(payload)
    return _TtsWire(body=decoded, audio_url=audio_url, provider_request_id=request_id)


def _tts_audio_ref(payload: dict[str, object]) -> tuple[bytes | None, str | None]:
    output = payload.get("output")
    if isinstance(output, dict):
        parsed = _tts_audio_value(output.get("audio"))
        if parsed is not None:
            return parsed
    extracted = _content_text(payload.get("choices"))
    if extracted:
        parsed = _tts_audio_value(extracted)
        if parsed is not None:
            return parsed
    raise ProviderError("MODEL_UNAVAILABLE")


def _tts_audio_value(audio: object) -> tuple[bytes | None, str | None] | None:
    if isinstance(audio, str) and audio:
        return _tts_audio_string(audio)
    if isinstance(audio, dict):
        url = audio.get("url")
        if isinstance(url, str) and url:
            return (None, url)
        data = audio.get("data")
        if isinstance(data, str) and data:
            return _tts_audio_string(data)
    return None


def _tts_audio_string(value: str) -> tuple[bytes | None, str | None]:
    if value.startswith("http://") or value.startswith("https://"):
        return (None, value)
    encoded = _strip_data_url(value)
    try:
        return (base64.b64decode(encoded, validate=True), None)
    except (ValueError, binascii.Error) as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc


def _ensure_playback_m4a(body: bytes) -> bytes:
    if _is_m4a(body):
        return body
    return _ffmpeg_to_m4a(body)


def _is_m4a(body: bytes) -> bool:
    return len(body) >= 16 and body[4:8] == b"ftyp"


def _ffmpeg_to_m4a(body: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise InvalidSpeech("tts remux unavailable")
    with tempfile.TemporaryDirectory(prefix="kelin-tts-") as tmp:
        src = Path(tmp) / "in.bin"
        dst = Path(tmp) / "out.m4a"
        src.write_bytes(body)
        try:
            completed = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(src),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-movflags",
                    "+faststart",
                    str(dst),
                ],
                check=False,
                capture_output=True,
                timeout=_TTS_REMUX_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise InvalidSpeech("tts remux timed out") from exc
        if completed.returncode != 0 or not dst.is_file():
            raise InvalidSpeech("tts remux failed")
        converted = dst.read_bytes()
    if not _is_m4a(converted):
        raise InvalidSpeech("tts remux is not m4a")
    return converted


def _strip_data_url(value: str) -> str:
    marker = "base64,"
    if marker in value:
        return value.split(marker, 1)[1]
    return value


def _transcript_from_response(response: httpx.Response) -> Transcript:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if not isinstance(payload, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    text = _asr_text(payload)
    language = _asr_language(payload)
    try:
        return Transcript(
            text=text,
            language=language,
            provider_request_id=_response_id(response),
        )
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc


def _asr_text(payload: dict[str, object]) -> str:
    output = payload.get("output")
    if isinstance(output, dict):
        text = output.get("text")
        if isinstance(text, str):
            return text.strip()
        choices = output.get("choices")
        extracted = _content_text(choices)
        if extracted is not None:
            return extracted
    extracted = _content_text(payload.get("choices"))
    if extracted is not None:
        return extracted
    raise ProviderError("MODEL_UNAVAILABLE")


def _content_text(choices: object) -> str | None:
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return " ".join(parts).strip()
    return None


def _asr_language(payload: dict[str, object]) -> str:
    output = payload.get("output")
    if isinstance(output, dict):
        language = output.get("language")
        if isinstance(language, str) and len(language) >= 2:
            return language[:16]
    return "zh"


def _image_payload(body: bytes, model: str, prompt_version: str) -> dict[str, object]:
    encoded = base64.b64encode(body).decode("ascii")
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": _load_prompt(prompt_version)},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Inspect this JPEG. Return JSON only."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                ],
            },
        ],
        "response_format": {"type": "json_object"},
    }


def _safety_output_from_response(response: httpx.Response) -> SafetyOutput:
    try:
        return SafetyOutput.model_validate(_message_json_object(response))
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc


def _vision_output_from_response(response: httpx.Response) -> VisionOutput:
    try:
        return VisionOutput.model_validate(_message_json_object(response))
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
