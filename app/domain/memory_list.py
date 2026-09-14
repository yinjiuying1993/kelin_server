"""Memory list filter mapping. Spec §§12.1, iOS MemoryFilter."""

from __future__ import annotations

from app.schemas.memory import MemoryFilter

RELATIONSHIP_TYPES: tuple[str, ...] = ("preference", "relation", "emotion")


def memory_types_for_filter(memory_filter: MemoryFilter) -> tuple[str, ...] | None:
    """None means all types. relationship maps preference/relation/emotion."""
    if memory_filter == "all":
        return None
    if memory_filter == "relationship":
        return RELATIONSHIP_TYPES
    return (memory_filter,)
