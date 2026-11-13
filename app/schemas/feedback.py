"""POST /feedback request and the id-only success body."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.feedback import FEEDBACK_TEXT_MAX
from app.schemas.envelope import EnvelopeError


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedbackRequest(_ForbidExtra):
    client_id: UUID
    text: str

    @field_validator("text")
    @classmethod
    def _trim_then_count(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 1 or len(trimmed) > FEEDBACK_TEXT_MAX:
            raise ValueError("invalid text length")
        return trimmed


class FeedbackAccepted(_ForbidExtra):
    id: UUID


class FeedbackSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: FeedbackAccepted
    error: None = None
    request_id: str
    server_time: str


class FeedbackErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
