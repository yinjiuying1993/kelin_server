"""POST /devices public contract. Spec §§14.6, 14.11."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.envelope import EnvelopeError

DeviceEnvironment = Literal["sandbox", "production"]


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterDeviceRequest(_ForbidExtra):
    client_id: UUID
    installation_id: UUID
    apns_token: str = Field(min_length=16, max_length=256)
    environment: DeviceEnvironment
    enabled: bool = True
    app_version: str | None = Field(default=None, min_length=1, max_length=32)
    locale: str | None = Field(default=None, min_length=2, max_length=32)


class DeviceRegistration(_ForbidExtra):
    device_id: UUID
    enabled: bool
    updated_at: str


class DeviceRegisterSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: DeviceRegistration
    error: None = None
    request_id: str
    server_time: str


class DeviceRegisterErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
