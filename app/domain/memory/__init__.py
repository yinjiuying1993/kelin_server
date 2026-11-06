"""Memory admit, merge, and recall pure functions. No IO."""

from app.domain.memory.admit import admit, pick_window_writes
from app.domain.memory.constants import (
    EXTRACT_ROUNDS,
    MEMORY_CONFIDENCE_MIN,
    MEMORY_PER_WINDOW_MAX,
    MEMORY_RECALL_MAX,
    RULES_VERSION,
)
from app.domain.memory.recall import build_recall_context, recall_score
from app.domain.memory.salience import compute_salience
from app.domain.memory.types import (
    AdmitContext,
    MemoryCandidate,
    MemoryDecision,
    MemoryIndexItem,
    RecallItem,
)

__all__ = [
    "EXTRACT_ROUNDS",
    "MEMORY_CONFIDENCE_MIN",
    "MEMORY_PER_WINDOW_MAX",
    "MEMORY_RECALL_MAX",
    "RULES_VERSION",
    "AdmitContext",
    "MemoryCandidate",
    "MemoryDecision",
    "MemoryIndexItem",
    "RecallItem",
    "admit",
    "build_recall_context",
    "compute_salience",
    "pick_window_writes",
    "recall_score",
]
