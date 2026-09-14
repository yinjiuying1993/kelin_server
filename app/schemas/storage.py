"""POST /storage/sight-upload-url contract. Spec §11.3."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.sight_upload import ALLOWED_MIME, MAX_SIZE_BYTES, MIN_SIZE_BYTES, PUT_METHOD
from app.schemas.envelope import EnvelopeError


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SightUploadUrlRequest(_ForbidExtra):
    client_id: UUID
    feed_id: UUID
    mime_type: Literal["image/jpeg"]
    size_bytes: int = Field(ge=MIN_SIZE_BYTES, le=MAX_SIZE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SightUploadUrlResult(_ForbidExtra):
    upload_session_id: UUID
    method: Literal["PUT"] = PUT_METHOD
    url: str
    headers: dict[str, str]
    object_path: str
    expires_at: str
    max_size_bytes: int = Field(ge=MIN_SIZE_BYTES, le=MAX_SIZE_BYTES)


class SightUploadSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: SightUploadUrlResult
    error: None = None
    request_id: str
    server_time: str


class SightUploadErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str


_CONTENT_TYPE_HEADER = {"content-type": ALLOWED_MIME}


def put_headers() -> dict[str, str]:
    return dict(_CONTENT_TYPE_HEADER)
