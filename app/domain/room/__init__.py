"""Room projection: V1 four-piece plus V0 letter/layers until S4-T04."""

from app.domain.room.legacy import (
    LETTER_TITLE_KEYS,
    P08_STATUS_LAYERS,
    STATUS_LAYER,
    LetterType,
    ProjectedRoomLetter,
    RoomLetterFact,
    RoomProjection,
    project_room,
    project_room_layers,
    project_room_letter,
)
from app.domain.room.letters import LETTER_PRIORITY, pick_letter_key
from app.domain.room.projector import project
from app.domain.room.types import ProjectionInput, RoomProjectionPatch

__all__ = [
    "LETTER_PRIORITY",
    "LETTER_TITLE_KEYS",
    "P08_STATUS_LAYERS",
    "STATUS_LAYER",
    "LetterType",
    "ProjectedRoomLetter",
    "ProjectionInput",
    "RoomLetterFact",
    "RoomProjection",
    "RoomProjectionPatch",
    "pick_letter_key",
    "project",
    "project_room",
    "project_room_layers",
    "project_room_letter",
]
