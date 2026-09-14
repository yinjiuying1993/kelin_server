"""Memory mutation hashes. Spec §§12.2–12.4, 14.11."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from app.schemas.memory import MemoryClearRequest, MemoryDeleteRequest, MemoryPatchRequest

PATCH_OPERATION = "memories.patch"
DELETE_OPERATION = "memories.delete"
CLEAR_OPERATION = "memories.clear"


def patch_request_hash(memory_id: UUID, body: MemoryPatchRequest) -> str:
    payload: dict[str, object] = {
        "action": body.action,
        "expected_version": body.expected_version,
        "memory_id": str(memory_id),
        "summary": body.summary,
    }
    return _hash(payload)


def delete_request_hash(memory_id: UUID, body: MemoryDeleteRequest) -> str:
    payload: dict[str, object] = {
        "expected_version": body.expected_version,
        "memory_id": str(memory_id),
    }
    return _hash(payload)


def clear_request_hash(body: MemoryClearRequest) -> str:
    payload: dict[str, object] = {"confirm": body.confirm}
    return _hash(payload)


def _hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
