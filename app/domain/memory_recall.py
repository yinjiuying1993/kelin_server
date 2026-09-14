"""Generation recall gates. Spec §§8.3, 12.3, 16.3, 21.7.5.

Sealed/deleted memories must leave Prompt, citations, SourceBadge reads,
and report candidates. Messages stay; display filters memory refs on read.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.schemas.jsonb import SourceRef

PROMPT_MEMORY_LIMIT = 20
PROMPT_STYLE_SAMPLE_LIMIT = 20
REPORT_MEMORY_CANDIDATE_LIMIT = 5


def memory_ids_of(refs: Sequence[SourceRef]) -> tuple[UUID, ...]:
    return tuple(ref.id for ref in refs if ref.type == "memory" and ref.id is not None)


def visible_source_refs(
    refs: Sequence[SourceRef],
    *,
    active_memory_ids: frozenset[UUID],
) -> tuple[SourceRef, ...]:
    visible: list[SourceRef] = []
    for ref in refs:
        if ref.type == "memory":
            if ref.id is not None and ref.id in active_memory_ids:
                visible.append(ref)
            continue
        visible.append(ref)
    return tuple(visible)
