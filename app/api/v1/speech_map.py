"""Map transcribe settlement to the public TranscribeResult. HTTP DTO only."""

from __future__ import annotations

from app.domain.speech import TTS_OUTPUT_MIME
from app.schemas.speech import (
    SynthesizeResult,
    TranscribeResult,
    TranscriptResource,
)
from app.schemas.speech_audio import SpeechAudioResource
from app.schemas.spirit import MutationPatch
from app.services.speech import SynthesizeSettlement, TranscribeSettlement


def transcribe_result_from_settlement(settlement: TranscribeSettlement) -> TranscribeResult:
    return TranscribeResult(
        resource=TranscriptResource(
            type="transcript",
            text=settlement.text,
            duration_ms=settlement.duration_ms,
            language=settlement.language,
            provider_request_id=settlement.provider_request_id,
        ),
        patch=MutationPatch(snapshot_version=settlement.snapshot_version),
        quotas=list(settlement.quotas),
    )


def synthesize_result_from_settlement(settlement: SynthesizeSettlement) -> SynthesizeResult:
    return SynthesizeResult(
        resource=SpeechAudioResource(
            type="speech_audio",
            audio_url=settlement.audio_url,
            mime=TTS_OUTPUT_MIME,
            duration_ms=settlement.duration_ms,
            expires_at=settlement.expires_at,
            cache_hit=settlement.cache_hit,
        ),
        patch=MutationPatch(snapshot_version=settlement.snapshot_version),
        quotas=list(settlement.quotas),
    )
