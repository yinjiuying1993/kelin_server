from __future__ import annotations

from uuid import uuid4

from app.domain.memory_mutation import (
    clear_request_hash,
    delete_request_hash,
    patch_request_hash,
)
from app.schemas.memory import MemoryClearRequest, MemoryDeleteRequest, MemoryPatchRequest


def test_patch_hash_changes_with_action_and_summary() -> None:
    memory_id = uuid4()
    correct = MemoryPatchRequest.model_validate(
        {
            "client_id": str(uuid4()),
            "expected_version": 1,
            "action": "correct",
            "summary": "请叫我阿年",
        }
    )
    other_summary = MemoryPatchRequest.model_validate(
        {
            "client_id": str(uuid4()),
            "expected_version": 1,
            "action": "correct",
            "summary": "请叫我小年",
        }
    )
    seal = MemoryPatchRequest.model_validate(
        {"client_id": str(uuid4()), "expected_version": 1, "action": "seal"}
    )
    assert patch_request_hash(memory_id, correct) != patch_request_hash(memory_id, other_summary)
    assert patch_request_hash(memory_id, correct) != patch_request_hash(memory_id, seal)
    assert patch_request_hash(memory_id, correct) == patch_request_hash(memory_id, correct)


def test_delete_and_clear_hashes_ignore_client_id() -> None:
    memory_id = uuid4()
    first = MemoryDeleteRequest.model_validate({"client_id": str(uuid4()), "expected_version": 2})
    second = MemoryDeleteRequest.model_validate({"client_id": str(uuid4()), "expected_version": 2})
    assert delete_request_hash(memory_id, first) == delete_request_hash(memory_id, second)
    clear = MemoryClearRequest.model_validate(
        {"client_id": str(uuid4()), "confirm": "CLEAR_ALL_MEMORIES"}
    )
    other = MemoryClearRequest.model_validate(
        {"client_id": str(uuid4()), "confirm": "CLEAR_ALL_MEMORIES"}
    )
    assert clear_request_hash(clear) == clear_request_hash(other)
