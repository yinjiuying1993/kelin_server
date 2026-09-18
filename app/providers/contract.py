"""Locked Bailian provider contract. Spec §§3.2, 5.3, 16.1, 16.5.

Aliases and timeouts are configuration, not hardcoded vendor model IDs or secrets.
Live calls stay blocked until BAILIAN_API_KEY and the capability alias are set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings

ProviderCapability = Literal["chat", "extract", "asr", "tts", "vision", "safety", "search"]
ProviderFailureKind = Literal[
    "timeout",
    "unavailable",
    "connect",
    "http_5xx",
    "http_429",
    "quota_exceeded",
]

PROVIDER_TIMEOUT_SECONDS: dict[ProviderCapability, int] = {
    "chat": 18,
    "extract": 30,
    "asr": 22,
    "tts": 18,
    "vision": 28,
    "safety": 28,
    "search": 12,
}
PROVIDER_CONDITIONAL_RETRY_COUNT = 1
PROVIDER_RETRY_ON: frozenset[str] = frozenset({"connect", "http_429", "http_5xx"})
PROVIDER_CIRCUIT_WINDOW_SECONDS = 300
PROVIDER_CIRCUIT_OPEN_SECONDS = 120
PROVIDER_CIRCUIT_MIN_SAMPLES = 2
PROVIDER_CIRCUIT_ERROR_RATE = 0.5
# Voice turns synthesize after chat; client budget must cover both upstream deadlines.
CLIENT_CHAT_TIMEOUT_SECONDS = 48

_FAILURE_MAP: dict[ProviderFailureKind, tuple[str, int, bool]] = {
    "timeout": ("PROVIDER_TIMEOUT", 504, True),
    "unavailable": ("MODEL_UNAVAILABLE", 503, True),
    "connect": ("MODEL_UNAVAILABLE", 503, True),
    "http_5xx": ("MODEL_UNAVAILABLE", 503, True),
    "http_429": ("RATE_LIMITED", 429, True),
    "quota_exceeded": ("QUOTA_EXCEEDED", 429, False),
}


@dataclass(frozen=True, slots=True)
class ProviderErrorMapping:
    code: str
    status_code: int
    retryable: bool


@dataclass(frozen=True, slots=True)
class LiveProviderReadiness:
    chat: bool
    extract: bool
    search: bool
    vision: bool
    safety: bool
    blocked: bool
    missing: tuple[str, ...]


def map_provider_failure(kind: ProviderFailureKind) -> ProviderErrorMapping:
    code, status_code, retryable = _FAILURE_MAP[kind]
    return ProviderErrorMapping(code=code, status_code=status_code, retryable=retryable)


def http_status_for_provider_code(code: str) -> tuple[int, bool]:
    for mapped_code, status_code, retryable in _FAILURE_MAP.values():
        if mapped_code == code:
            return status_code, retryable
    return 503, True


def model_alias(settings: Settings, capability: ProviderCapability) -> str | None:
    field = {
        "chat": settings.bailian_chat_model,
        "extract": settings.bailian_extract_model,
        "asr": settings.bailian_asr_model,
        "tts": settings.bailian_tts_model,
        "vision": settings.bailian_vision_model,
        "safety": settings.bailian_safety_model,
        "search": settings.bailian_search_model,
    }[capability]
    return field


def live_provider_readiness(settings: Settings) -> LiveProviderReadiness:
    missing: list[str] = []
    if settings.bailian_api_key is None:
        missing.append("BAILIAN_API_KEY")
    if settings.bailian_chat_model is None:
        missing.append("BAILIAN_CHAT_MODEL")
    if settings.bailian_extract_model is None:
        missing.append("BAILIAN_EXTRACT_MODEL")
    chat = settings.bailian_api_key is not None and settings.bailian_chat_model is not None
    extract = settings.bailian_api_key is not None and settings.bailian_extract_model is not None
    search = (
        chat
        and settings.bailian_search_enabled is True
        and settings.bailian_search_model is not None
    )
    vision = settings.bailian_api_key is not None and settings.bailian_vision_model is not None
    safety = settings.bailian_api_key is not None and settings.bailian_safety_model is not None
    if settings.bailian_search_enabled and settings.bailian_search_model is None:
        missing.append("BAILIAN_SEARCH_MODEL")
    return LiveProviderReadiness(
        chat=chat,
        extract=extract,
        search=search,
        vision=vision,
        safety=safety,
        blocked=not chat,
        missing=tuple(missing),
    )
