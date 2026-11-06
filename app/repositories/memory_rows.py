"""Shared memory and idempotency rows used by recall and debug."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MemoryRow:
    id: uuid.UUID
    type: str
    summary: str
    tags: tuple[str, ...]
    salience: int
    confidence: float
    status: str
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    inserted: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None
