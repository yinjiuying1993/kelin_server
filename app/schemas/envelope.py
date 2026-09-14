from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EnvelopeError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: Any | None = None
    error: EnvelopeError | None = None
    request_id: str
    server_time: str = Field(..., description="HTTP response time, ISO 8601 UTC")
