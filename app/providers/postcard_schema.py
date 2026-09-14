"""Postcard Provider whitelist. Spec §§8.9, 16.2.

extra=forbid. Private social fields cannot appear on input or output.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.visit_postcards import postcard_text_is_safe
from app.providers.errors import ProviderError
from app.schemas.social_api import PRIVATE_SOCIAL_FIELD_NAMES, PUBLIC_VISIT_CONTEXT_FIELD_NAMES
from app.schemas.spirit import SpiritStage

PostcardRole = Literal["visitor", "host"]


class PostcardSafetyRejected(Exception):
    """Generated postcard text failed public-only Safety."""


class _ForbidExtra(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PostcardPublicSubject(_ForbidExtra):
    title: str = Field(min_length=1, max_length=40)
    stage: SpiritStage
    weather: str = Field(min_length=1, max_length=32)
    public_marks: list[str] = Field(default_factory=list)


class PostcardGenerateInput(_ForbidExtra):
    role: PostcardRole
    subject: PostcardPublicSubject
    counterpart: PostcardPublicSubject | None = None
    npc_id: str | None = Field(default=None, max_length=32)


class PostcardGenerateOutput(_ForbidExtra):
    text: str = Field(min_length=1, max_length=300)


def assert_public_postcard_payload(payload: object) -> None:
    if not isinstance(payload, dict):
        raise ProviderError("MODEL_UNAVAILABLE")
    keys = set(payload)
    if keys & PRIVATE_SOCIAL_FIELD_NAMES:
        raise ProviderError("MODEL_UNAVAILABLE")
    subject = payload.get("subject")
    if isinstance(subject, dict) and set(subject) - PUBLIC_VISIT_CONTEXT_FIELD_NAMES:
        raise ProviderError("MODEL_UNAVAILABLE")
    counterpart = payload.get("counterpart")
    if isinstance(counterpart, dict) and set(counterpart) - PUBLIC_VISIT_CONTEXT_FIELD_NAMES:
        raise ProviderError("MODEL_UNAVAILABLE")


def require_postcard_output(value: object) -> PostcardGenerateOutput:
    try:
        if isinstance(value, PostcardGenerateOutput):
            parsed = PostcardGenerateOutput.model_validate(value.model_dump())
        else:
            parsed = PostcardGenerateOutput.model_validate(value)
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
    if not postcard_text_is_safe(parsed.text):
        raise PostcardSafetyRejected
    return parsed
