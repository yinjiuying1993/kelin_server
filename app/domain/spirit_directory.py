"""Chat directory card. Five fields, and only three outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from app.domain.spirit_profile import avatar_url


class DirectoryOutcome(Enum):
    MISSING = "missing"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SpiritDirectoryCard:
    spirit_id: UUID
    owner_user_id: UUID
    display_name: str
    avatar_url: str
    available: bool


@dataclass(frozen=True)
class DirectoryAnswer:
    outcome: DirectoryOutcome
    card: SpiritDirectoryCard | None


@dataclass(frozen=True)
class DirectoryRow:
    spirit_id: UUID
    owner_user_id: UUID
    display_name: str
    avatar_key: str
    available: bool


def classify_directory(row: DirectoryRow | None, *, viewer_id: UUID) -> DirectoryAnswer:
    if row is None or row.owner_user_id == viewer_id:
        return DirectoryAnswer(DirectoryOutcome.MISSING, None)
    available = row.available
    card = SpiritDirectoryCard(
        spirit_id=row.spirit_id,
        owner_user_id=row.owner_user_id,
        display_name=row.display_name,
        avatar_url=avatar_url(row.avatar_key),
        available=available,
    )
    outcome = DirectoryOutcome.AVAILABLE if available else DirectoryOutcome.UNAVAILABLE
    return DirectoryAnswer(outcome, card)
