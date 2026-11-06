"""Self-test tickets. The snapshot is the skill body from the moment the test starts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

SELF_TEST_TICKET_TTL = timedelta(minutes=30)


@dataclass(frozen=True)
class GeneratedSkill:
    skill_slug: str
    body: str


@dataclass(frozen=True)
class StoredTicket:
    ticket_id: UUID
    spirit_id: UUID
    craft_id: UUID
    skill_slug: str
    snapshot_body: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedTicket:
    ticket_id: UUID
    expires_at: datetime


def ticket_is_expired(expires_at: datetime, now: datetime) -> bool:
    return expires_at <= now
