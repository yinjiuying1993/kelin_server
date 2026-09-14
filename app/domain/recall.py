"""POST /recall helpers. Spec §§8.7, 11.5, 14.11."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Final
from uuid import UUID

from app.schemas.recall import RecallRequest

RECALL_OPERATION: Final[str] = "recall"
RECALL_GROWTH_EVENT: Final[str] = "returned_from_lost"
RECALL_MUTATION_EVENT: Final[str] = "recall.returned"
RECALL_RELATION_SUMMARY: Final[str] = "你离开过"
RECALL_RELATION_SALIENCE: Final[int] = 80
RECALL_RELATION_CONFIDENCE: Final[float] = 1.0
RECALL_BOND_DELTA: Final[int] = 5
RECALL_MOOD_DELTA: Final[int] = 8
RECALL_CLOSENESS_DELTA: Final[int] = 2
RECALL_MEMORY_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")


def recall_request_hash(request: RecallRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"client_id"})
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def recall_relation_memory_id(*, owner_id: UUID, client_id: UUID) -> UUID:
    return uuid.uuid5(RECALL_MEMORY_NS, f"kelin:recall:{owner_id}:{client_id}")


def recall_growth_payload() -> dict[str, int]:
    return {
        "bond_delta": RECALL_BOND_DELTA,
        "mood_delta": RECALL_MOOD_DELTA,
        "closeness_delta": RECALL_CLOSENESS_DELTA,
    }
