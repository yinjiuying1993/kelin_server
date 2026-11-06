"""PATCH /spirit request/response. Spec §§5.5, 9.5."""

from __future__ import annotations

from typing import Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.envelope import EnvelopeError
from app.schemas.room import RoomPublic
from app.schemas.spirit import (
    MutationResource,
    QuotaUsage,
    SpiritPublic,
    UserPreferencesPublic,
)


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreferencesPatch(_ForbidExtra):
    tts_on: bool | None = None
    visit_on: bool | None = None
    timezone: str | None = None
    default_city: str | None = Field(default=None, max_length=40)
    location_weather_on: bool | None = None
    remote_search_on: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _iana(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or len(value) > 64:
            raise ValueError("timezone must be IANA")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be IANA") from exc
        return value

    @field_validator("default_city")
    @classmethod
    def _city(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped or len(stripped) > 40:
            raise ValueError("default_city length")
        return stripped


class PatchSpiritRequest(_ForbidExtra):
    client_id: UUID
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=20)
    preferences: PreferencesPatch | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> Self:
        if self.name is None and self.preferences is None:
            raise ValueError("name or preferences is required")
        if self.name is None and self.preferences is not None:
            if not self.preferences.model_fields_set:
                raise ValueError("name or preferences is required")
        return self


class SpiritPatchPatch(_ForbidExtra):
    snapshot_version: int = Field(ge=1)
    spirit: SpiritPublic
    preferences: UserPreferencesPublic
    room: RoomPublic
    report: None = None
    social: None = None
    memories_upsert: list[Any] = Field(default_factory=list)
    memory_tombstones: list[Any] = Field(default_factory=list)
    postcards_upsert: list[Any] = Field(default_factory=list)
    pact: None = None


class SpiritPatchResult(_ForbidExtra):
    resource: MutationResource
    patch: SpiritPatchPatch
    quotas: list[QuotaUsage] = Field(default_factory=list)
    events: list[Any] = Field(default_factory=list)


class SpiritPatchSuccessEnvelope(_ForbidExtra):
    ok: Literal[True] = True
    data: SpiritPatchResult
    error: None = None
    request_id: str
    server_time: str


class SpiritPatchErrorEnvelope(_ForbidExtra):
    ok: Literal[False] = False
    data: None = None
    error: EnvelopeError
    request_id: str
    server_time: str
