"""Shared TTS playback resource. Leaf schema: no domain imports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SpeechAudioResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["speech_audio"] = "speech_audio"
    audio_url: str = Field(min_length=1, max_length=2048)
    mime: Literal["audio/mp4"] = "audio/mp4"
    duration_ms: int = Field(ge=1)
    expires_at: str
    cache_hit: bool
