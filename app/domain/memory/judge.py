"""Keep the model's memory judgment. The only drop is an identical summary."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.memory.constants import ALLOWED_TYPES, SUMMARY_MAX


@dataclass(frozen=True, slots=True)
class JudgedMemory:
    memory_type: str
    summary: str


def judgment_memories(
    payload: object,
    existing_summaries: set[str],
) -> tuple[JudgedMemory, ...] | None:
    """Return memories to insert. None means the model payload is unusable."""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("memories")
    if not isinstance(raw, list):
        return None
    seen = {item.strip() for item in existing_summaries if item.strip()}
    kept: list[JudgedMemory] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        if not isinstance(summary, str):
            continue
        text = summary.strip()[:SUMMARY_MAX]
        if not text or text in seen:
            continue
        kind = item.get("type")
        memory_type = kind if isinstance(kind, str) and kind in ALLOWED_TYPES else "preference"
        seen.add(text)
        kept.append(JudgedMemory(memory_type=memory_type, summary=text))
    return tuple(kept)
