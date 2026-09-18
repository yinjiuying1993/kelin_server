"""POST /chat request/response contract. Spec §§8.2, 9.4, 10.1."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.envelope import EnvelopeError
from app.schemas.jsonb import SourceRef
from app.schemas.speech_audio import SpeechAudioResource
from app.schemas.spirit import MutationEvent, MutationPatch, QuotaUsage

ChatSource = Literal["text", "voice", "onboarding"]
GenerationSource = Literal["stub", "provider"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatContext(_ForbidExtra):
    """Client expression context only. local_hour is not used for quota or safety time."""

    timezone: str = Field(min_length=1, max_length=64)
    local_hour: int = Field(ge=0, le=23)
    weather: str = Field(min_length=1, max_length=32)
    city: str | None = Field(default=None, max_length=40)


class ChatRequest(_ForbidExtra):
    client_message_id: UUID
    content: str = Field(min_length=1, max_length=4000)
    source: ChatSource
    onboarding: bool = Field(
        description=(
            "Required so the server can distinguish S04 hatch turns from ordinary chat. "
            "Must match server onboarding state. onboarding=true must not increment "
            "ordinary_dialogue_rounds. Only a complete user+spirit pair advances onboarding_step."
        )
    )
    context: ChatContext


class ChatMessagePublic(_ForbidExtra):
    id: UUID
    content: str = Field(min_length=1, max_length=4000)
    source_refs: list[SourceRef] = Field(default_factory=list)


class ChatUsage(_ForbidExtra):
    input_units: int = Field(ge=0)
    output_units: int = Field(ge=0)


class ChatTurnResource(_ForbidExtra):
    type: Literal["chat_turn"] = "chat_turn"
    onboarding: bool = Field(
        description=(
            "Echo of the settled turn kind. onboarding=true must not increment "
            "ordinary_dialogue_rounds."
        )
    )
    generation_source: GenerationSource = Field(
        description=(
            "stub is a deterministic stand-in and must not be presented as the live chat provider. "
            "provider is reserved for a later wired model."
        )
    )
    user_message: ChatMessagePublic
    spirit_message: ChatMessagePublic
    conversation_window_id: UUID = Field(
        description=(
            "Server-owned conversation window. Fewer than 3 ordinary turns may return an "
            "open window. Clients must not submit start/end message IDs as window bounds."
        )
    )
    should_extract: bool = Field(
        description="True only when the conversation window status is ready for extract."
    )
    speech_audio: SpeechAudioResource | None = Field(
        description=(
            "Filled after source=voice chat persists the spirit reply. Null for text turns, "
            "replies over 200 characters, TTS quota exhaustion, or synthesis failure. Chat "
            "still returns 200; clients play audio_url or fall back to text."
        )
    )
    usage: ChatUsage


class ChatTurnResult(_ForbidExtra):
    resource: ChatTurnResource
    patch: MutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class ChatSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: ChatTurnResult
    error: None = None
    request_id: str
    server_time: str


class ChatErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
