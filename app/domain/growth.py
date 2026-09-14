"""Growth event catalog. Spec §§6.4, 8.4–8.7, 8.5; P13-T01.

Trusted producers write `growth_events`. Clients submit facts only — never delta,
stage, status, or scholar marks. Unique key is `(source_type, source_id, event_type)`.
Payload is settlement scalars; message/prompt/image bodies are forbidden.

P16 visit.settle and P20 POST /recall are wired. Scheduler time_passed is wired.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal
from uuid import UUID

GrowthEventType = Literal[
    "chat_completed",
    "feed_accepted",
    "promise_completed",
    "pact_answered",
    "pact_completed",
    "memory_added",
    "visit_completed",
    "returned_from_lost",
    "time_passed",
]
GrowthSourceType = Literal[
    "chat_turn",
    "feed",
    "memory",
    "pact",
    "visit",
    "recall",
    "scheduler",
]
ProducerStatus = Literal["wired", "reserved"]
GrowthPayloadShape = Literal["empty", "applied_vitals", "trait_delta"]
GrowthEffect = Literal[
    "interact",
    "hunger",
    "energy",
    "mood",
    "bond",
    "trait",
    "status",
    "memory",
    "scholar_mark",
    "postcard",
]
GrowthPatchField = Literal[
    "spirit",
    "room",
    "memories_upsert",
    "quotas",
    "events",
]

GROWTH_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "chat_completed",
        "feed_accepted",
        "promise_completed",
        "pact_answered",
        "pact_completed",
        "memory_added",
        "visit_completed",
        "returned_from_lost",
        "time_passed",
    }
)
GROWTH_SOURCE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "chat_turn",
        "feed",
        "memory",
        "pact",
        "visit",
        "recall",
        "scheduler",
    }
)
GROWTH_PAYLOAD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "hunger_delta",
        "energy_delta",
        "mood_delta",
        "bond_delta",
        "closeness_delta",
        "curiosity_delta",
        "sharpness_delta",
        "nocturnal_delta",
        "stubborn_delta",
    }
)
FORBIDDEN_GROWTH_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "text",
        "content",
        "prompt",
        "reply",
        "message",
        "summary",
        "image",
        "audio",
        "delta",
        "stage",
        "status",
    }
)
CLIENT_DELTA_FIELD_NAMES: Final[frozenset[str]] = GROWTH_PAYLOAD_FIELDS | frozenset(
    {
        "hunger",
        "energy",
        "mood",
        "bond",
        "closeness",
        "curiosity",
        "sharpness",
        "nocturnal",
        "stubborn",
        "stage",
        "scholar_marks",
        "status",
    }
)
TRAIT_DELTA_RANGE: Final[tuple[int, int]] = (-2, 2)
GROWTH_VALUE_MIN: Final[int] = 0
GROWTH_VALUE_MAX: Final[int] = 100
TRAIT_COLUMNS: Final[tuple[str, ...]] = (
    "closeness",
    "curiosity",
    "sharpness",
    "nocturnal",
    "stubborn",
)


def compact_growth_payload(values: dict[str, int]) -> dict[str, int]:
    compact = {key: int(delta) for key, delta in values.items() if delta}
    unknown = set(compact) - GROWTH_PAYLOAD_FIELDS
    if unknown:
        raise ValueError("growth payload contains keys outside the catalog")
    return compact


def clamp_growth_value(current: int, delta: int) -> int:
    return max(GROWTH_VALUE_MIN, min(GROWTH_VALUE_MAX, current + delta))


MUTATION_EVENT_FOR_GROWTH: Final[dict[str, str]] = {
    "feed_accepted": "feed.accepted",
    "promise_completed": "promise.completed",
    "pact_answered": "pact.answered",
    "pact_completed": "pact.completed",
    "returned_from_lost": "recall.returned",
}


@dataclass(frozen=True, slots=True)
class GrowthCatalogEntry:
    event_type: GrowthEventType
    source_type: GrowthSourceType
    source_id: str
    producers: tuple[str, ...]
    producer_status: ProducerStatus
    payload: GrowthPayloadShape
    effects: tuple[GrowthEffect, ...]
    patch: tuple[GrowthPatchField, ...]


GROWTH_CATALOG: Final[tuple[GrowthCatalogEntry, ...]] = (
    GrowthCatalogEntry(
        event_type="chat_completed",
        source_type="chat_turn",
        source_id="messages.id of the persisted user turn",
        producers=("POST /chat",),
        producer_status="wired",
        payload="empty",
        effects=("interact",),
        patch=("spirit", "events"),
    ),
    GrowthCatalogEntry(
        event_type="feed_accepted",
        source_type="feed",
        source_id="feeds.id",
        producers=(
            "POST /feed food|knowledge|emotion",
            "POST /feed sight location accepted",
            "POST /feed promise create",
            "POST /moderate-sight accepted",
        ),
        producer_status="wired",
        payload="applied_vitals",
        effects=("hunger", "energy", "mood", "interact", "memory"),
        patch=("spirit", "room", "memories_upsert", "quotas", "events"),
    ),
    GrowthCatalogEntry(
        event_type="promise_completed",
        source_type="feed",
        source_id="feeds.id of the completed promise",
        producers=("POST /feeds/{id}/complete",),
        producer_status="wired",
        payload="applied_vitals",
        effects=("bond", "interact"),
        patch=("spirit", "room", "events"),
    ),
    GrowthCatalogEntry(
        event_type="pact_answered",
        source_type="pact",
        source_id="pact_sessions.id of the scored session",
        producers=("POST /pact-answer last missing question (P15)",),
        producer_status="wired",
        payload="empty",
        effects=(),
        patch=("events",),
    ),
    GrowthCatalogEntry(
        event_type="pact_completed",
        source_type="pact",
        source_id="pacts.id",
        producers=("POST /pact-answer last missing question finalize (P15)",),
        producer_status="wired",
        payload="applied_vitals",
        effects=("bond", "scholar_mark"),
        patch=("spirit", "events"),
    ),
    GrowthCatalogEntry(
        event_type="memory_added",
        source_type="memory",
        source_id="memories.id from extract (not feed-created memories)",
        producers=("POST /extract",),
        producer_status="wired",
        payload="trait_delta",
        effects=("trait",),
        patch=("spirit", "memories_upsert"),
    ),
    GrowthCatalogEntry(
        event_type="visit_completed",
        source_type="visit",
        source_id="visits.id",
        producers=("visit.settle worker (P16)",),
        producer_status="wired",
        payload="empty",
        effects=("status", "postcard", "interact"),
        patch=("spirit", "room", "events"),
    ),
    GrowthCatalogEntry(
        event_type="returned_from_lost",
        source_type="recall",
        source_id="recall client_id",
        producers=("POST /recall",),
        producer_status="wired",
        payload="applied_vitals",
        effects=("status", "memory", "trait", "interact"),
        patch=("spirit", "room", "memories_upsert", "quotas", "events"),
    ),
    GrowthCatalogEntry(
        event_type="time_passed",
        source_type="scheduler",
        source_id="outbox/job id of one state.settle tick",
        producers=("state.settle scheduler / bootstrap settle (T04)",),
        producer_status="wired",
        payload="empty",
        effects=("status",),
        patch=("spirit", "room"),
    ),
)


def growth_catalog_by_event() -> dict[str, GrowthCatalogEntry]:
    return {entry.event_type: entry for entry in GROWTH_CATALOG}


def growth_dedupe_key(
    *, source_type: str, source_id: UUID, event_type: str
) -> tuple[str, UUID, str]:
    if source_type not in GROWTH_SOURCE_TYPES:
        raise ValueError("growth source_type is not in the catalog")
    if event_type not in GROWTH_EVENT_TYPES:
        raise ValueError("growth event_type is not in the catalog")
    expected = growth_catalog_by_event()[event_type]
    if source_type != expected.source_type:
        raise ValueError("growth source_type does not match the event catalog")
    return (source_type, source_id, event_type)
