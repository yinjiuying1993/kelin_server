"""Onboarding chat-turn counting rules. Spec §§8.2, 8.3, 10.1."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from app.domain.spirit import SpiritCreateResult
from app.schemas.chat import ChatRequest
from app.schemas.jsonb import SourceRef
from app.schemas.spirit import QuotaUsage

GenerationSource = Literal["stub", "provider"]

CHAT_TURN_OPERATION = "chat.turn"
CHAT_TURN_LEASE_SECONDS = 120
CHAT_TURN_RETRY_AFTER_MS = 500
ORDINARY_WINDOW_READY_ROUNDS = 3
ONBOARDING_STUB_REPLIES: tuple[str, ...] = ("嗯。", "好。", "记下了。", "继续。", "好的。")
UNKNOWN_SOURCE_REPLY = "这件事我还不知道"


@dataclass(frozen=True, slots=True)
class GeneratedTurn:
    content: str
    generation_source: GenerationSource
    input_units: int
    output_units: int
    source_refs: tuple[SourceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatTurnSettlement:
    spirit_id: uuid.UUID
    user_message_id: uuid.UUID
    spirit_message_id: uuid.UUID
    conversation_window_id: uuid.UUID
    onboarding: bool
    generation_source: GenerationSource
    should_extract: bool
    onboarding_step: int
    ordinary_dialogue_rounds: int
    version: int
    replayed: bool
    last_interact_at: datetime
    user_content: str
    spirit_content: str
    input_units: int
    output_units: int
    spirit: SpiritCreateResult
    spirit_source_refs: tuple[SourceRef, ...] = ()
    quotas: tuple[QuotaUsage, ...] = ()


class ChatGenerationError(Exception):
    """Stub/provider failed before a spirit reply existed. Must not advance step."""


class ChatTurnGenerator(Protocol):
    def generate(self, *, onboarding: bool, onboarding_step: int) -> GeneratedTurn: ...


class OnboardingStubGenerator:
    """Deterministic stand-in. Must not echo user text or claim a live provider."""

    def generate(self, *, onboarding: bool, onboarding_step: int) -> GeneratedTurn:
        del onboarding
        index = min(max(onboarding_step, 0), len(ONBOARDING_STUB_REPLIES) - 1)
        return GeneratedTurn(
            content=ONBOARDING_STUB_REPLIES[index],
            generation_source="stub",
            input_units=0,
            output_units=0,
        )


def next_onboarding_step(
    current: int,
    *,
    onboarding: bool,
    pair_complete: bool,
) -> int:
    if not pair_complete or not onboarding:
        return current
    if current >= 5:
        return current
    return current + 1


def chat_turn_request_hash(request: ChatRequest) -> str:
    payload = {
        "content": request.content,
        "source": request.source,
        "onboarding": request.onboarding,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def next_ordinary_dialogue_rounds(
    current: int,
    *,
    onboarding: bool,
    pair_complete: bool,
) -> int:
    if not pair_complete or onboarding:
        return current
    return current + 1


def conversation_window_status(*, onboarding: bool, user_round_count: int) -> str:
    if not onboarding and user_round_count >= ORDINARY_WINDOW_READY_ROUNDS:
        return "ready"
    return "open"


def should_extract_for_window(*, onboarding: bool, user_round_count: int) -> bool:
    return (
        conversation_window_status(onboarding=onboarding, user_round_count=user_round_count)
        == "ready"
    )


def should_extract_for_status(status: str) -> bool:
    return status == "ready"
