"""Extract settlement rules. Spec §§8.3, 10.3, 16.2; PRD §4.2."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.spirit import SpiritCreateResult
from app.providers.types import (
    ExtractMemoryDraft,
    ExtractOutput,
    ExtractStyleSample,
    TraitDimension,
)

MEMORY_CONFIDENCE_FLOOR = 0.75
TRAIT_MIN = 0
TRAIT_MAX = 100
MAX_EXTRACT_MEMORIES = 2
MAX_EXTRACT_STYLE_SAMPLES = 1
MAX_TAG_COUNT = 8
MAX_TAG_LENGTH = 32
TRAIT_DIMENSIONS: tuple[TraitDimension, ...] = (
    "closeness",
    "curiosity",
    "sharpness",
    "nocturnal",
    "stubborn",
)
ExtractStatus = Literal["processing", "extracted"]


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: uuid.UUID
    type: str
    summary: str
    tags: tuple[str, ...]
    salience: int
    confidence: float
    status: str
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StyleSampleRecord:
    id: uuid.UUID
    kind: str
    text: str
    weight: int
    status: str


@dataclass(frozen=True, slots=True)
class ExtractSettlement:
    window_id: uuid.UUID
    status: ExtractStatus
    spirit: SpiritCreateResult
    memories: tuple[MemoryRecord, ...]
    style_samples: tuple[StyleSampleRecord, ...]
    replayed: bool


def sanitize_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        item = raw.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item[:MAX_TAG_LENGTH])
        if len(cleaned) >= MAX_TAG_COUNT:
            break
    return cleaned


def accepted_memories(output: ExtractOutput) -> list[ExtractMemoryDraft]:
    accepted: list[ExtractMemoryDraft] = []
    for draft in output.memories:
        summary = draft.summary.strip()
        if not summary or draft.confidence < MEMORY_CONFIDENCE_FLOOR:
            continue
        accepted.append(
            draft.model_copy(
                update={
                    "summary": summary[:500],
                    "tags": sanitize_tags(draft.tags),
                }
            )
        )
        if len(accepted) >= MAX_EXTRACT_MEMORIES:
            break
    return accepted


def accepted_style_samples(output: ExtractOutput) -> list[ExtractStyleSample]:
    samples: list[ExtractStyleSample] = []
    for sample in output.style_samples:
        text = sample.text.strip()
        if not text:
            continue
        samples.append(sample.model_copy(update={"text": text[:100]}))
        if len(samples) >= MAX_EXTRACT_STYLE_SAMPLES:
            break
    return samples


def extract_output_hash(
    memories: list[ExtractMemoryDraft],
    style_samples: list[ExtractStyleSample],
) -> str:
    payload = {
        "memories": [item.model_dump(mode="json") for item in memories],
        "style_samples": [item.model_dump(mode="json") for item in style_samples],
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def clamp_trait(current: int, delta: int) -> int:
    return max(TRAIT_MIN, min(TRAIT_MAX, current + delta))


def accumulated_trait_deltas(memories: list[ExtractMemoryDraft]) -> dict[TraitDimension, int]:
    deltas: dict[TraitDimension, int] = {name: 0 for name in TRAIT_DIMENSIONS}
    for draft in memories:
        patch = draft.personality_delta
        if patch is None or patch.value == 0:
            continue
        deltas[patch.dimension] += patch.value
    return deltas
