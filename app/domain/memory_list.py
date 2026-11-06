"""Memory list filter mapping. Spec §§12.1; V1 four types only."""

from __future__ import annotations

from app.providers.types import MemoryType
from app.schemas.memory import MemoryFilter

LIST_DB_TYPES: tuple[str, ...] = ("preference", "knowledge", "emotion", "relationship")
_PUBLIC_FROM_DB: dict[str, MemoryType] = {
    "preference": "preference",
    "knowledge": "knowledge",
    "emotion": "emotion",
    "relationship": "relationship",
}


def memory_types_for_filter(memory_filter: MemoryFilter) -> tuple[str, ...]:
    """DB type tuples stored as the public V1 four types."""
    if memory_filter == "all":
        return LIST_DB_TYPES
    return (memory_filter,)


def public_memory_type(raw: str) -> MemoryType | None:
    return _PUBLIC_FROM_DB.get(raw)


def require_public_memory_type(raw: str) -> MemoryType:
    mapped = public_memory_type(raw)
    if mapped is None:
        raise RuntimeError("invalid memory type")
    return mapped


def db_memory_type(public: MemoryType) -> str:
    return public
