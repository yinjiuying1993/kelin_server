"""Map extract settlement to the public ExtractResult. HTTP DTO only."""

from __future__ import annotations

from app.api.v1.spirit_map import utc_z, utc_z_optional
from app.domain.extract import ExtractSettlement, MemoryRecord, StyleSampleRecord
from app.domain.spirit import SpiritCreateResult
from app.schemas.extract import ExtractResource, ExtractResult, StyleSamplePublic
from app.schemas.memory import MemoryPublic
from app.schemas.spirit import MutationPatch, OnboardingState, SpiritPublic


def extract_result_from_settlement(settlement: ExtractSettlement) -> ExtractResult:
    spirit = _spirit_public(settlement.spirit)
    completed = utc_z_optional(settlement.spirit.onboarding_completed_at)
    memories = [_memory_public(item) for item in settlement.memories]
    styles = [_style_public(item) for item in settlement.style_samples]
    return ExtractResult(
        resource=ExtractResource(
            type="extract",
            conversation_window_id=settlement.window_id,
            status=settlement.status,
            memories=memories,
            style_samples=styles,
        ),
        patch=MutationPatch(
            snapshot_version=settlement.spirit.version,
            spirit=spirit,
            room=None,
            onboarding=OnboardingState(
                required=settlement.spirit.onboarding_completed_at is None,
                step=settlement.spirit.onboarding_step,
                completed_at=completed,
            ),
            memories_upsert=list(memories),
        ),
    )


def _spirit_public(result: SpiritCreateResult) -> SpiritPublic:
    completed = utc_z_optional(result.onboarding_completed_at)
    return SpiritPublic.model_validate(
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


def _memory_public(item: MemoryRecord) -> MemoryPublic:
    return MemoryPublic.model_validate(
        {
            "id": item.id,
            "type": item.type,
            "summary": item.summary,
            "tags": list(item.tags),
            "salience": item.salience,
            "confidence": item.confidence,
            "status": item.status,
            "version": item.version,
            "created_at": utc_z(item.created_at),
        }
    )


def _style_public(item: StyleSampleRecord) -> StyleSamplePublic:
    return StyleSamplePublic.model_validate(
        {
            "id": item.id,
            "kind": item.kind,
            "text": item.text,
            "weight": item.weight,
            "status": item.status,
        }
    )
