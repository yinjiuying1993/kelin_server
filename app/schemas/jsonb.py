"""Pydantic schema boundary for chat citation JSON."""

from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter, model_validator


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRef(_ForbidExtra):
    """Verified citation summary only. Web refs are HTTPS URL + fetch time."""

    type: Literal["memory", "pact", "builtin_bank", "web"]
    id: UUID | None = None
    url: str | None = None
    fetched_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def shape_matches_type(self) -> Self:
        if self.type == "web":
            if self.id is not None or not self.url or not self.url.startswith("https://"):
                raise ValueError("web citation needs an https url and no id")
            return self
        if self.id is None or self.url is not None or self.fetched_at is not None:
            raise ValueError("local citation needs id only")
        return self


_SOURCE_REFS_ADAPTER: TypeAdapter[list[SourceRef]] = TypeAdapter(list[SourceRef])


def parse_source_refs(value: object) -> list[SourceRef]:
    return _SOURCE_REFS_ADAPTER.validate_python(value)
