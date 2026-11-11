"""Owner spirit page. Invite code, stage, and days stay off the public profile."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.invite_code import INVITE_CODE_PATTERN
from app.schemas.craft import RankName
from app.schemas.envelope import EnvelopeError
from app.schemas.spirit import SpiritStage


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpiritPageSkill(_ForbidExtra):
    title: str = Field(min_length=1)
    rank_name: RankName | None = None


class SpiritOwnerPage(_ForbidExtra):
    id: UUID
    name: str = Field(min_length=1, max_length=20)
    nickname: str | None = Field(default=None, min_length=1, max_length=12)
    avatar_url: str = Field(min_length=1)
    avatar_is_default: bool
    stage: SpiritStage
    companion_days: int = Field(ge=0)
    invite_code: str = Field(pattern=INVITE_CODE_PATTERN)
    help_count: int = Field(ge=0)
    skills: list[SpiritPageSkill]


class NicknameSaveRequest(_ForbidExtra):
    nickname: str = Field(max_length=40)


class AvatarUploadRequest(_ForbidExtra):
    content_base64: str = Field(min_length=1)


class SpiritOwnerPageEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: SpiritOwnerPage
    error: None = None
    request_id: str
    server_time: str


class SpiritOwnerPageErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
