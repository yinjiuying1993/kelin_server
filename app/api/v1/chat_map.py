"""Map chat-turn settlement to the public ChatTurnResult. HTTP DTO only."""

from __future__ import annotations

from app.api.v1.public_map import (
    hatched_at_public,
    room_public_v1,
    spirit_public_from_mapping,
    utc_z,
)
from app.domain.chat import ChatTurnSettlement
from app.domain.speech import TTS_OUTPUT_MIME
from app.schemas.chat import ChatMessagePublic, ChatTurnResource, ChatTurnResult, ChatUsage
from app.schemas.speech_audio import SpeechAudioResource
from app.schemas.spirit import MutationPatch


def chat_turn_result_from_settlement(settlement: ChatTurnSettlement) -> ChatTurnResult:
    result = settlement.spirit
    spirit = spirit_public_from_mapping(
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
            "version": result.version,
            "hatched_at": hatched_at_public(
                hatched_at=result.hatched_at, created_at=result.created_at
            ),
            "created_at": utc_z(result.created_at),
        }
    )
    return ChatTurnResult(
        resource=ChatTurnResource(
            type="chat_turn",
            generation_source=settlement.generation_source,
            user_message=ChatMessagePublic(
                id=settlement.user_message_id,
                content=settlement.user_content,
                source_refs=[],
            ),
            spirit_message=ChatMessagePublic(
                id=settlement.spirit_message_id,
                content=settlement.spirit_content,
                source_refs=[],
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
        room=settlement.room
        or room_public_v1(
            stage=result.stage,
            status=result.status,
            letter_key=None,
            primary_emotion_label=None,
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
