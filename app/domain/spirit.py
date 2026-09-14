"""Spirit create domain: egg traits, payload hash, result. Spec §§6.2, 6.3, 9.3."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, time

from app.schemas.spirit import CreateSpiritRequest, Egg

SPIRIT_CREATE_OPERATION = "spirits.create"


@dataclass(frozen=True, slots=True)
class EggTraits:
    closeness: int
    curiosity: int
    sharpness: int
    nocturnal: int
    stubborn: int


EGG_TRAITS: dict[str, EggTraits] = {
    "warm": EggTraits(65, 55, 35, 40, 40),
    "cold": EggTraits(35, 45, 70, 60, 55),
    "wild": EggTraits(50, 50, 50, 50, 50),
}


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


def traits_for_egg(egg: Egg | str) -> EggTraits:
    traits = EGG_TRAITS.get(str(egg))
    if traits is None:
        raise KeyError(egg)
    return traits


def create_spirit_request_hash(request: CreateSpiritRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"client_id"})
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
