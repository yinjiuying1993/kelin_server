"""POST /transcribe and POST /synthesize contract. Spec §§10.4, 10.5, 14.12."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.speech import (
    ASR_MAX_DURATION_MS,
    ASR_MIN_DURATION_MS,
    TTS_VOICE_DEFAULT,
)
from app.schemas.envelope import EnvelopeError
from app.schemas.speech_audio import SpeechAudioResource
from app.schemas.spirit import MutationEvent, MutationPatch, QuotaUsage


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TranscribeForm(_ForbidExtra):
    client_id: UUID


class SynthesizeRequest(_ForbidExtra):
    client_id: UUID
    message_id: UUID
    voice_profile: Literal["default"] = Field(
        default=TTS_VOICE_DEFAULT,
        description=(
            "V0 allows only default. Clients must not send text; the server reads the owned "
            "spirit message body and rejects more than 200 characters."
        ),
    )


class TranscriptResource(_ForbidExtra):
    type: Literal["transcript"] = "transcript"
    text: str = Field(min_length=1, max_length=4000)
    duration_ms: int = Field(ge=ASR_MIN_DURATION_MS, le=ASR_MAX_DURATION_MS)
    language: str = Field(min_length=2, max_length=16)
    provider_request_id: str | None = Field(default=None, max_length=128)


class TranscribeResult(_ForbidExtra):
    resource: TranscriptResource
    patch: MutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class SynthesizeResult(_ForbidExtra):
    resource: SpeechAudioResource
    patch: MutationPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[MutationEvent] = Field(default_factory=list)


class TranscribeSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: TranscribeResult
    error: None = None
    request_id: str
    server_time: str


class SynthesizeSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: SynthesizeResult
    error: None = None
    request_id: str
    server_time: str


class SpeechErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
