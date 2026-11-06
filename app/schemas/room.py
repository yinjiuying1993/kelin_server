"""V1 room four-piece public contract. Architecture §3, §11; 房间投影施工说明."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StageImageKey = Literal["whelp_image", "formed_image", "awake_image"]
RoomStatus = Literal["home", "away"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoomPublic(_ForbidExtra):
    stage_image_key: StageImageKey
    letter_key: str | None = None
    text_status: str = Field(min_length=1, max_length=64)
    status: RoomStatus
