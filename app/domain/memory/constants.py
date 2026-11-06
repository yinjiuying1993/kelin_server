"""Memory domain constants. No IO."""

from __future__ import annotations

RULES_VERSION = "kelin-v1-rules-1"
EXTRACT_ROUNDS = 3
MEMORY_PER_WINDOW_MAX = 2
MEMORY_CONFIDENCE_MIN = 0.75
MEMORY_ACTIVE_MAX = 200
MEMORY_RECALL_MAX = 6
SUMMARY_MAX = 500
TOPIC_MIN_PARTS = 4

ALLOWED_TYPES: frozenset[str] = frozenset(
    {"preference", "knowledge", "emotion", "relationship"}
)
FORBIDDEN_TYPES: frozenset[str] = frozenset(
    {"speech", "sight", "weather", "style", "relation"}
)
ALLOWED_SIGNALS: frozenset[str] = frozenset(
    {"explicit", "repeated", "commitment", "teachable", "stable_emotion"}
)
RECALL_STATUSES: frozenset[str] = frozenset({"active"})
SIGNAL_PRIORITY: tuple[str, ...] = (
    "explicit",
    "commitment",
    "stable_emotion",
    "teachable",
    "repeated",
)
SIGNAL_BASE: dict[str, int] = {
    "explicit": 85,
    "commitment": 75,
    "stable_emotion": 60,
    "teachable": 55,
    "repeated": 50,
}
TYPE_SALIENCE_FLOOR: dict[str, int] = {
    "preference": 50,
    "knowledge": 50,
    "emotion": 55,
    "relationship": 55,
}
STAGE_RECALL_CAP: dict[str, int] = {
    "whelp": 3,
    "formed": 5,
    "awake": 6,
}
KNOWLEDGE_EVENTS: frozenset[str] = frozenset({"feed.knowledge.accepted"})
EMOTION_EVENTS: frozenset[str] = frozenset({"feed.emotion.accepted"})
RELATIONSHIP_EVENTS: frozenset[str] = frozenset(
    {
        "pact.created",
        "pact.completed",
        "promise.created",
        "promise.completed",
        "recall.completed",
    }
)
UNCERTAINTY_WORDS: tuple[str, ...] = ("可能", "也许", "大概", "似乎")
EMPHASIS_WORDS: tuple[str, ...] = ("很重要", "一定", "必须")
