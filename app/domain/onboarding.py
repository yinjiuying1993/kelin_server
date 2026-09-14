"""Onboarding complete rules. Spec §§8.2, 9.4.

Completion is a database fact: step=5 and five persisted onboarding pairs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from app.domain.spirit import SpiritCreateResult
from app.schemas.onboarding import CompleteOnboardingRequest

ONBOARDING_COMPLETE_OPERATION = "onboarding.complete"
REQUIRED_ONBOARDING_ROUNDS = 5


@dataclass(frozen=True, slots=True)
class OnboardingCompleteSettlement:
    spirit: SpiritCreateResult
    ordinary_dialogue_rounds: int
    updated_at: datetime
    replayed: bool


def complete_onboarding_request_hash(request: CompleteOnboardingRequest) -> str:
    payload = {"expected_spirit_version": request.expected_spirit_version}
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def onboarding_ready_to_hatch(*, step: int, complete_rounds: int) -> bool:
    return step == 5 and complete_rounds >= REQUIRED_ONBOARDING_ROUNDS
