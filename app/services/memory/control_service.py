"""Serialize memory-control settlements for the central event payload."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.repositories import memory as memory_repo
from app.services.memory.legacy import MemoryMutationSettlement


def settlement_payload(settlement: MemoryMutationSettlement) -> dict[str, Any]:
    memory = None
    if settlement.memory is not None:
        row = settlement.memory
        memory = {
            "id": str(row.id),
            "spirit_id": str(row.spirit_id),
            "type": row.type,
            "summary": row.summary,
            "tags": list(row.tags),
            "salience": row.salience,
            "confidence": float(row.confidence),
            "status": row.status,
            "version": row.version,
            "created_at": row.created_at.isoformat(),
            "source_message_id": (
                str(row.source_message_id) if row.source_message_id is not None else None
            ),
            "deleted_at": row.deleted_at.isoformat() if row.deleted_at is not None else None,
            "sealed_at": row.sealed_at.isoformat() if row.sealed_at is not None else None,
        }
    tombstone = None
    if settlement.tombstone is not None:
        tombstone = {
            "id": str(settlement.tombstone.id),
            "deleted_at": settlement.tombstone.deleted_at.isoformat(),
        }
    return {
        "kind": settlement.kind,
        "changed": settlement.changed,
        "clear_client_id": (
            str(settlement.clear_client_id) if settlement.clear_client_id is not None else None
        ),
        "memory": memory,
        "tombstone": tombstone,
    }


def settlement_from_payload(
    payload: dict[str, Any], *, snapshot_version: int
) -> MemoryMutationSettlement:
    memory = _memory(payload.get("memory"))
    tombstone = _tombstone(payload.get("tombstone"))
    clear_raw = payload.get("clear_client_id")
    return MemoryMutationSettlement(
        snapshot_version=snapshot_version,
        kind=str(payload["kind"]),
        memory=memory,
        tombstone=tombstone,
        clear_client_id=uuid.UUID(str(clear_raw)) if clear_raw else None,
        changed=bool(payload.get("changed")),
    )


def _memory(value: object) -> memory_repo.MemoryMutationRow | None:
    if not isinstance(value, dict):
        return None
    source = value.get("source_message_id")
    return memory_repo.MemoryMutationRow(
        id=uuid.UUID(str(value["id"])),
        spirit_id=uuid.UUID(str(value["spirit_id"])),
        type=str(value["type"]),
        summary=str(value["summary"]),
        tags=[str(item) for item in value.get("tags") or []],
        salience=int(value["salience"]),
        confidence=float(value["confidence"]),
        status=str(value["status"]),
        version=int(value["version"]),
        created_at=_time(value["created_at"]),
        source_message_id=uuid.UUID(str(source)) if source else None,
        deleted_at=_time(value["deleted_at"]) if value.get("deleted_at") else None,
        sealed_at=_time(value["sealed_at"]) if value.get("sealed_at") else None,
    )


def _tombstone(value: object) -> memory_repo.MemoryTombstoneRow | None:
    if not isinstance(value, dict):
        return None
    return memory_repo.MemoryTombstoneRow(
        id=uuid.UUID(str(value["id"])),
        deleted_at=_time(value["deleted_at"]),
    )


def _time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("memory control timestamp must be timezone-aware")
    return parsed
