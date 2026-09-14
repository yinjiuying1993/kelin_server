"""POST /moderate-sight contract. Spec §§11.4, 14.12."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.sight_moderate import SightPropKind
from app.schemas.envelope import EnvelopeError
from app.schemas.feed import FeedMutationResult


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModerateSightRequest(_ForbidExtra):
    client_id: UUID
    feed_id: UUID
    upload_session_id: UUID


class ModerateSightResult(FeedMutationResult):
    prop: SightPropKind | None = None


class ModerateSightSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: ModerateSightResult
    error: None = None
    request_id: str
    server_time: str


class ModerateSightErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
