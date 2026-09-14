"""Map memory mutation settlement to MutationResult. HTTP DTO only."""

from __future__ import annotations

from app.domain.cursor import utc_iso
from app.providers.types import MemoryType
from app.repositories.memory import MemoryMutationRow
from app.schemas.memory import (
    MemoryClearResource,
    MemoryMutationResult,
    MemoryPublic,
    MemoryResource,
    MemoryStatus,
    MemoryTombstone,
)
from app.schemas.spirit import MutationPatch
from app.services.memory import MemoryMutationSettlement


def memory_mutation_from_settlement(settlement: MemoryMutationSettlement) -> MemoryMutationResult:
    if settlement.kind == "clear":
        if settlement.clear_client_id is None:
            raise RuntimeError("clear settlement is missing client_id")
        return MemoryMutationResult(
            resource=MemoryClearResource(id=settlement.clear_client_id),
            patch=MutationPatch(snapshot_version=settlement.snapshot_version),
        )
    if settlement.memory is None:
        raise RuntimeError("memory settlement is missing the memory row")
    resource = MemoryResource(
        id=settlement.memory.id,
        version=settlement.memory.version,
        status=_memory_status(settlement.memory.status),
    )
    upsert: list[MemoryPublic] = []
    tombstones: list[MemoryTombstone] = []
    if settlement.kind in {"correct", "seal"}:
        upsert = [_public_memory(settlement.memory)]
    elif settlement.tombstone is not None:
        tombstones = [
            MemoryTombstone(
                id=settlement.tombstone.id,
                deleted_at=utc_iso(settlement.tombstone.deleted_at),
            )
        ]
    return MemoryMutationResult(
        resource=resource,
        patch=MutationPatch(
            snapshot_version=settlement.snapshot_version,
            memories_upsert=upsert,
            memory_tombstones=tombstones,
        ),
    )


def _public_memory(row: MemoryMutationRow) -> MemoryPublic:
    return MemoryPublic(
        id=row.id,
        type=_memory_type(row.type),
        summary=row.summary,
        tags=list(row.tags),
        salience=row.salience,
        confidence=row.confidence,
        status=_memory_status(row.status),
        version=row.version,
        created_at=utc_iso(row.created_at),
    )


def _memory_type(value: str) -> MemoryType:
    mapping: dict[str, MemoryType] = {
        "preference": "preference",
        "knowledge": "knowledge",
        "emotion": "emotion",
        "relation": "relation",
        "speech": "speech",
        "sight": "sight",
    }
    mapped = mapping.get(value)
    if mapped is None:
        raise RuntimeError("invalid memory type")
    return mapped


def _memory_status(value: str) -> MemoryStatus:
    if value == "active":
        return "active"
    if value == "sealed":
        return "sealed"
    if value == "deleted":
        return "deleted"
    raise RuntimeError("invalid memory status")
