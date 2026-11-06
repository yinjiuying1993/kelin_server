"""Map extract settlement to the public ExtractResult. HTTP DTO only."""

from __future__ import annotations

from app.api.v1.public_map import (
    hatched_at_public,
    room_public_v1,
    spirit_public_from_mapping,
    utc_z,
)
from app.domain.extract import ExtractSettlement, MemoryRecord
from app.domain.memory_list import require_public_memory_type
from app.domain.spirit import SpiritCreateResult
from app.schemas.extract import ExtractResource, ExtractResult
from app.schemas.memory import MemoryPublic
from app.schemas.spirit import MutationPatch, SpiritPublic


def extract_result_from_settlement(settlement: ExtractSettlement) -> ExtractResult:
    spirit = _spirit_public(settlement.spirit)
    memories = [_memory_public(item) for item in settlement.memories]
    return ExtractResult(
        resource=ExtractResource(
            type="extract",
            conversation_window_id=settlement.window_id,
            status=settlement.status,
            memories=memories,
        ),
        patch=MutationPatch(
            snapshot_version=settlement.spirit.version,
            spirit=spirit,
            room=room_public_v1(
                stage=settlement.spirit.stage,
                status=settlement.spirit.status,
                letter_key=None,
                primary_emotion_label=None,
            ),
            memories_upsert=list(memories),
        ),
    )


def _spirit_public(result: SpiritCreateResult) -> SpiritPublic:
    return spirit_public_from_mapping(
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


def _memory_public(item: MemoryRecord) -> MemoryPublic:
    return MemoryPublic.model_validate(
        {
            "id": item.id,
            "type": require_public_memory_type(item.type),
            "summary": item.summary,
            "tags": list(item.tags),
            "salience": item.salience,
            "confidence": item.confidence,
            "status": item.status,
            "version": item.version,
            "created_at": utc_z(item.created_at),
        }
    )
