"""Chat Mode selection. First matching row wins; model cannot change it."""

from __future__ import annotations

from app.domain.central.types import ChatMode, OrchestratorRequest

_SAFETY_FLAGS = frozenset({"high_risk", "safety", "safety_high_risk"})


def choose_chat_mode(request: OrchestratorRequest) -> ChatMode:
    """Safety, lost, away, and knowledge only. Hunger and emotion do not pick a mode."""
    flags = frozenset(request.safety_flags)
    if flags & _SAFETY_FLAGS:
        return "safety"
    if request.status == "lost":
        return "recall_only"
    if request.status in {"study", "away"}:
        return "away_note"
    if request.knowledge_question:
        return "knowledge"
    return "companion"
