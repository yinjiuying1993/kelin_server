"""Map chat-turn settlement to the public ChatTurnResult. HTTP DTO only."""

from __future__ import annotations

from app.api.v1.spirit_map import utc_z, utc_z_optional
from app.domain.chat import ChatTurnSettlement
from app.domain.speech import TTS_OUTPUT_MIME
from app.schemas.chat import ChatMessagePublic, ChatTurnResource, ChatTurnResult, ChatUsage
from app.schemas.speech_audio import SpeechAudioResource
from app.schemas.spirit import MutationPatch, OnboardingState, SpiritPublic


def chat_turn_result_from_settlement(settlement: ChatTurnSettlement) -> ChatTurnResult:
    result = settlement.spirit
    completed = utc_z_optional(result.onboarding_completed_at)
    spirit = SpiritPublic.model_validate(
        {
            "id": result.spirit_id,
            "name": result.name,
            "egg": result.egg,
            "invite_code": result.invite_code,
            "closeness": result.closeness,
            "curiosity": result.curiosity,
            "sharpness": result.sharpness,
            "nocturnal": result.nocturnal,
            "stubborn": result.stubborn,
            "hunger": result.hunger,
            "energy": result.energy,
            "mood": result.mood,
            "bond": result.bond,
            "stage": result.stage,
            "status": result.status,
            "scholar_marks": list(result.scholar_marks),
            "version": result.version,
            "onboarding_step": result.onboarding_step,
            "onboarding_completed_at": completed,
            "hatched_at": utc_z_optional(result.hatched_at),
            "created_at": utc_z(result.created_at),
        }
    )
    return ChatTurnResult(
        resource=ChatTurnResource(
            type="chat_turn",
            onboarding=settlement.onboarding,
            generation_source=settlement.generation_source,
            user_message=ChatMessagePublic(
                id=settlement.user_message_id,
                content=settlement.user_content,
                source_refs=[],
            ),
            spirit_message=ChatMessagePublic(
                id=settlement.spirit_message_id,
                content=settlement.spirit_content,
                source_refs=list(settlement.spirit_source_refs),
            ),
            conversation_window_id=settlement.conversation_window_id,
            should_extract=settlement.should_extract,
            speech_audio=_speech_audio_resource(settlement),
            usage=ChatUsage(
                input_units=settlement.input_units,
                output_units=settlement.output_units,
            ),
        ),
        patch=MutationPatch(
            snapshot_version=result.version,
            spirit=spirit,
            room=None,
            onboarding=OnboardingState(
                required=result.onboarding_completed_at is None,
                step=result.onboarding_step,
                completed_at=completed,
            ),
        ),
        quotas=list(settlement.quotas),
    )


def _speech_audio_resource(settlement: ChatTurnSettlement) -> SpeechAudioResource | None:
    attached = settlement.speech_audio
    if attached is None:
        return None
    return SpeechAudioResource(
        type="speech_audio",
        audio_url=attached.audio_url,
        mime=TTS_OUTPUT_MIME,
        duration_ms=attached.duration_ms,
        expires_at=attached.expires_at,
        cache_hit=attached.cache_hit,
    )
