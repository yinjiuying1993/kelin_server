"""Spirit create domain: payload hash and result. Spec §§6.2, 6.3, 9.3."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, time

from app.schemas.spirit import CreateSpiritRequest

SPIRIT_CREATE_OPERATION = "spirits.create"
CURRENT_CONSENT_DOCUMENT_VERSION = "2026-11-12"


@dataclass(frozen=True, slots=True)
class SpiritCreateResult:
    spirit_id: uuid.UUID
    client_id: uuid.UUID
    name: str
    egg: str
    invite_code: str
    closeness: int
    curiosity: int
    sharpness: int
    nocturnal: int
    stubborn: int
    hunger: int
    energy: int
    mood: int
    bond: int
    stage: str
    status: str
    scholar_marks: tuple[str, ...]
    version: int
    onboarding_step: int
    onboarding_completed_at: datetime | None
    hatched_at: datetime | None
    created_at: datetime
    tts_on: bool
    push_on: bool
    visit_on: bool
    dnd_start: time
    dnd_end: time
    timezone: str
    default_city: str | None
    location_weather_on: bool
    remote_search_on: bool


def consent_document_versions_are_current(request: CreateSpiritRequest) -> bool:
    current = CURRENT_CONSENT_DOCUMENT_VERSION
    consents = request.consents
    return (
        consents.ai_disclosure.document_version == current
        and consents.data_notice.document_version == current
        and consents.user_terms.document_version == current
    )


def create_spirit_request_hash(request: CreateSpiritRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"client_id"})
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
