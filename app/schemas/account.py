"""DELETE /account DeletionResult. Spec §14.9."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.envelope import EnvelopeError

DeletionStatus = Literal["accepted"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeleteAccountRequest(_ForbidExtra):
    client_id: UUID
    confirm: str = Field(min_length=1, max_length=64)


class DeletionResult(_ForbidExtra):
    deletion_id: UUID
    status: DeletionStatus
    requested_at: str


class AccountDeleteSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: DeletionResult
    error: None = None
    request_id: str
    server_time: str


class AccountDeleteErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
