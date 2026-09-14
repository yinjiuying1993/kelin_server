"""Room letter/layers projection. Spec §8.9.1.

Letter is a key+id pointer. Callers must not pass message, memory, pact, or postcard bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.domain.spirit_state import IDLE_STUDY_OR_AWAY, SpiritStatus, require_aware

P08_STATUS_LAYERS: dict[SpiritStatus, str] = {
    "home": "spirit",
    "study": "study",
    "away": "away",
    "lost": "lost",
}

LetterType = Literal["lost", "promise", "pact", "postcard", "care"]

LETTER_TITLE_KEYS: dict[LetterType, str] = {
    "lost": "room.letter.lost",
    "promise": "room.letter.promise",
    "pact": "room.letter.pact",
    "postcard": "room.letter.postcard",
    "care": "room.letter.care",
}

STATUS_LAYER: dict[SpiritStatus, str] = P08_STATUS_LAYERS


@dataclass(frozen=True, slots=True)
class RoomLetterFact:
    resource_id: UUID
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProjectedRoomLetter:
    type: LetterType
    resource_id: UUID
    title_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RoomProjection:
    layers: tuple[str, ...]
    letter: ProjectedRoomLetter | None
    unread_footprint_count: int


def _letter(kind: LetterType, resource_id: UUID, occurred_at: datetime) -> ProjectedRoomLetter:
    require_aware(occurred_at, field="occurred_at")
    return ProjectedRoomLetter(
        type=kind,
        resource_id=resource_id,
        title_key=LETTER_TITLE_KEYS[kind],
        occurred_at=occurred_at,
    )


def project_room_letter(
    *,
    status: SpiritStatus,
    spirit_id: UUID,
    last_interact_at: datetime,
    now: datetime,
    due_promise: RoomLetterFact | None,
    pact_recap: RoomLetterFact | None,
    unread_postcard: RoomLetterFact | None,
) -> ProjectedRoomLetter | None:
    """Priority: lost > promise > pact > postcard > care. No private bodies."""

    require_aware(now, field="now")
    require_aware(last_interact_at, field="last_interact_at")
    if status == "lost":
        return _letter("lost", spirit_id, last_interact_at)
    if due_promise is not None:
        return _letter("promise", due_promise.resource_id, due_promise.occurred_at)
    if pact_recap is not None:
        return _letter("pact", pact_recap.resource_id, pact_recap.occurred_at)
    if unread_postcard is not None:
        return _letter("postcard", unread_postcard.resource_id, unread_postcard.occurred_at)
    idle = now - last_interact_at
    if idle >= IDLE_STUDY_OR_AWAY:
        return _letter("care", spirit_id, last_interact_at)
    return None


def project_room_layers(
    *,
    status: SpiritStatus,
    pending_sight: bool,
    has_letter: bool,
    unread_footprint_count: int,
    has_scholar_marks: bool,
) -> tuple[str, ...]:
    layers = [STATUS_LAYER[status]]
    if pending_sight:
        layers.append("pending_sight")
    if has_letter:
        layers.append("letter")
    if unread_footprint_count > 0:
        layers.append("footprints")
    if has_scholar_marks:
        layers.append("scholar")
    return tuple(layers)


def project_room(
    *,
    status: SpiritStatus,
    spirit_id: UUID,
    last_interact_at: datetime,
    now: datetime,
    scholar_marks: tuple[str, ...],
    due_promise: RoomLetterFact | None,
    pact_recap: RoomLetterFact | None,
    unread_postcard: RoomLetterFact | None,
    pending_sight: bool,
    unread_footprint_count: int,
) -> RoomProjection:
    letter = project_room_letter(
        status=status,
        spirit_id=spirit_id,
        last_interact_at=last_interact_at,
        now=now,
        due_promise=due_promise,
        pact_recap=pact_recap,
        unread_postcard=unread_postcard,
    )
    layers = project_room_layers(
        status=status,
        pending_sight=pending_sight,
        has_letter=letter is not None,
        unread_footprint_count=unread_footprint_count,
        has_scholar_marks=bool(scholar_marks),
    )
    return RoomProjection(
        layers=layers,
        letter=letter,
        unread_footprint_count=max(0, unread_footprint_count),
    )
