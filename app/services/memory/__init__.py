"""Memory module. List/control stay here; window extract is async."""

from __future__ import annotations

from app.services.memory.legacy import (
    MemoryMutationSettlement,
    clear_owned_memories,
    delete_owned_memory,
    load_memory_page,
    patch_owned_memory,
)
from app.services.memory.window_service import (
    EXTRACT_EVENT,
    DialogueCompletedFact,
    WindowAdvanceResult,
    extract_task_decision,
    on_dialogue_completed,
)

__all__ = [
    "DialogueCompletedFact",
    "EXTRACT_EVENT",
    "MemoryMutationSettlement",
    "WindowAdvanceResult",
    "clear_owned_memories",
    "delete_owned_memory",
    "extract_task_decision",
    "load_memory_page",
    "on_dialogue_completed",
    "patch_owned_memory",
]
