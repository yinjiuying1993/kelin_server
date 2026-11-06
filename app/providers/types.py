"""Provider DTOs. Spec §§8.3, 16.1–16.2. extra=forbid; no live-model IDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

StubMode = Literal["success", "delay", "cancel", "error"]
StubOutcome = Literal["success", "error", "cancelled"]
ProviderSource = Literal["stub", "provider"]
MemoryType = Literal["preference", "knowledge", "emotion", "relationship"]
MemorySignal = Literal["explicit", "repeated", "commitment", "teachable", "stable_emotion"]
ChatIntent = Literal[
    "safety",
    "recall_only",
    "away_note",
    "knowledge",
    "tired",
    "hungry",
    "companion",
]
TraitDimension = Literal["closeness", "curiosity", "sharpness", "nocturnal", "stubborn"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptMemory(_ForbidExtra):
    id: UUID
    type: MemoryType
    summary: str = Field(min_length=1, max_length=500)


class ChatInput(_ForbidExtra):
    content: str = Field(min_length=1)
    memories: list[PromptMemory] = Field(default_factory=list, max_length=20)
    emotion_context: str = ""
    thinking: bool = True


class ChatCitation(_ForbidExtra):
    type: Literal["memory"]
    id: UUID


class ChatOutput(_ForbidExtra):
    reply: str = Field(min_length=1)
    intent: ChatIntent | None = None
    citations: list[ChatCitation] = Field(default_factory=list)
    safety: Literal["allow"] = "allow"
    search_query: str | None = None
    tool: str | None = None
    tool_arg: str | None = None
    naming: bool = False
    given_name: str | None = None


class SearchInput(_ForbidExtra):
    query: str = Field(min_length=1, max_length=500)


class SearchResult(_ForbidExtra):
    url: str = Field(min_length=1, max_length=2048)
    title: str | None = Field(default=None, max_length=200)


class ExtractTurn(_ForbidExtra):
    """Server-owned window transcript. Clients never submit these turns or message IDs."""

    role: Literal["user", "spirit"]
    content: str = Field(min_length=1)


class ExtractInput(_ForbidExtra):
    conversation_window_id: UUID
    turns: list[ExtractTurn] = Field(default_factory=list)


class ExtractMemoryDraft(_ForbidExtra):
    type: MemoryType
    summary: str = Field(min_length=1, max_length=500)
    normalized_topic_key: str = Field(min_length=1, max_length=128)
    source_quote: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    signal: MemorySignal
    tags: list[str] = Field(default_factory=list)


class ExtractOutput(_ForbidExtra):
    memories: list[ExtractMemoryDraft] = Field(default_factory=list, max_length=2)


@dataclass(frozen=True, slots=True)
class SafetyInput:
    sha256: str
    size_bytes: int
    body: bytes


class SafetyOutput(_ForbidExtra):
    decision: Literal["allow", "block"]


@dataclass(frozen=True, slots=True)
class VisionInput:
    sha256: str
    size_bytes: int
    body: bytes


@dataclass(frozen=True, slots=True)
class ASRInput:
    mime_type: str
    size_bytes: int
    duration_ms: int
    sha256: str
    body: bytes


class Transcript(_ForbidExtra):
    text: str = Field(max_length=4000)
    language: str = Field(min_length=2, max_length=16)
    duration_ms: int | None = Field(default=None, ge=1)
    provider_request_id: str | None = Field(default=None, max_length=128)


@dataclass(frozen=True, slots=True)
class TTSInput:
    text: str
    voice_profile: str
    message_id: UUID


@dataclass(frozen=True, slots=True)
class AudioResult:
    body: bytes
    mime: str
    duration_ms: int
    provider_request_id: str | None = None


class VisionOutput(_ForbidExtra):
    summary: str = Field(min_length=1, max_length=500)
    prop: str = Field(min_length=1, max_length=32)


@dataclass(frozen=True, slots=True)
class StubStep:
    mode: StubMode
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class ProviderCallRecord:
    capability: Literal["chat"]
    source: ProviderSource
    mode: StubMode
    outcome: StubOutcome
    latency_ms: int
