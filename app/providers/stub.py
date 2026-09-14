"""Controllable in-process chat stub. Spec §§16.1, 21.5.

Deterministic stand-in for success, delay, cancel, error, and FIFO scripts.
Does not call a live model. Call records never include content or replies.
"""

from __future__ import annotations

import asyncio
from collections import deque
from time import perf_counter
from typing import Literal

from app.core.logging import get_logger
from app.domain.chat import ONBOARDING_STUB_REPLIES
from app.domain.sight_moderate import DEFAULT_SIGHT_PROP, DEFAULT_SIGHT_SUMMARY
from app.providers.errors import ProviderCancelled, ProviderError, ProviderStubUnsupported
from app.providers.types import (
    ASRInput,
    AudioResult,
    ChatInput,
    ChatOutput,
    ExtractInput,
    ExtractMemoryDraft,
    ExtractOutput,
    ExtractStyleSample,
    PersonalityDelta,
    ProviderCallRecord,
    SafetyInput,
    SafetyOutput,
    SearchInput,
    SearchResult,
    StubMode,
    StubStep,
    Transcript,
    TTSInput,
    VisionInput,
    VisionOutput,
)

STUB_SOURCE: Literal["stub"] = "stub"
STUB_LABEL = "deterministic provider stub; not a live chat provider"


class ControllableProviderStub:
    def __init__(self) -> None:
        self._script: deque[StubStep] = deque()
        self._moderation: deque[Literal["allow", "block", "error"]] = deque()
        self._lock = asyncio.Lock()
        self._calls: list[ProviderCallRecord] = []
        self._logger = get_logger(component="provider_stub")

    @property
    def source(self) -> Literal["stub"]:
        return STUB_SOURCE

    @property
    def label(self) -> str:
        return STUB_LABEL

    @property
    def calls(self) -> tuple[ProviderCallRecord, ...]:
        return tuple(self._calls)

    def enqueue(self, mode: StubMode, *, latency_ms: int = 0) -> None:
        if latency_ms < 0:
            raise ValueError("latency_ms must be >= 0")
        if mode == "delay" and latency_ms <= 0:
            raise ValueError("delay requires latency_ms > 0")
        self._script.append(StubStep(mode=mode, latency_ms=latency_ms))

    async def chat(self, value: ChatInput) -> ChatOutput:
        async with self._lock:
            return await self._chat_locked(value)

    async def extract(self, value: ExtractInput) -> ExtractOutput:
        payload = value if isinstance(value, ExtractInput) else ExtractInput.model_validate(value)
        user_turns = [turn for turn in payload.turns if turn.role == "user"]
        if not user_turns:
            return ExtractOutput()
        summary = user_turns[0].content.strip()[:500]
        if not summary:
            return ExtractOutput()
        sample_text = summary[:100]
        memories = [
            ExtractMemoryDraft(
                type="preference",
                summary=summary,
                tags=["extract"],
                salience=90,
                confidence=0.96,
                personality_delta=PersonalityDelta(dimension="closeness", value=1),
            )
        ]
        style_samples: list[ExtractStyleSample] = []
        if sample_text:
            style_samples = [ExtractStyleSample(kind="user_dialect", text=sample_text)]
        return ExtractOutput(memories=memories, style_samples=style_samples)

    async def transcribe(self, value: ASRInput | object) -> Transcript:
        del value
        raise ProviderStubUnsupported("transcribe")

    async def synthesize(self, value: TTSInput | object) -> AudioResult:
        del value
        raise ProviderStubUnsupported("synthesize")

    def enqueue_moderation(self, outcome: Literal["allow", "block", "error"]) -> None:
        self._moderation.append(outcome)

    async def vision(self, value: VisionInput | object) -> VisionOutput:
        del value
        outcome = self._moderation.popleft() if self._moderation else "allow"
        if outcome == "error":
            raise ProviderError("PROVIDER_TIMEOUT")
        return VisionOutput(summary=DEFAULT_SIGHT_SUMMARY, prop=DEFAULT_SIGHT_PROP)

    async def moderate(self, value: SafetyInput | object) -> SafetyOutput:
        del value
        outcome = self._moderation.popleft() if self._moderation else "allow"
        if outcome == "error":
            raise ProviderError("PROVIDER_TIMEOUT")
        return SafetyOutput(decision="block" if outcome == "block" else "allow")

    async def search(self, value: SearchInput) -> list[SearchResult]:
        del value
        raise ProviderStubUnsupported("search")

    async def _chat_locked(self, value: ChatInput) -> ChatOutput:
        step = self._script.popleft() if self._script else StubStep(mode="success")
        started = perf_counter()
        try:
            if step.mode == "delay":
                await asyncio.sleep(step.latency_ms / 1000)
            elif step.mode == "cancel":
                raise ProviderCancelled
            elif step.mode == "error":
                raise ProviderError("MODEL_UNAVAILABLE")
            output = ChatOutput(reply=_stub_reply(value.onboarding_step))
            self._finish(step, outcome="success", started=started)
            return output
        except asyncio.CancelledError:
            self._finish(step, outcome="cancelled", started=started)
            raise
        except ProviderCancelled:
            self._finish(step, outcome="cancelled", started=started)
            raise
        except ProviderError:
            self._finish(step, outcome="error", started=started)
            raise

    def _finish(
        self, step: StubStep, *, outcome: Literal["success", "error", "cancelled"], started: float
    ) -> None:
        latency_ms = max(0, int((perf_counter() - started) * 1000))
        record = ProviderCallRecord(
            capability="chat",
            source=STUB_SOURCE,
            mode=step.mode,
            outcome=outcome,
            latency_ms=latency_ms,
        )
        self._calls.append(record)
        self._logger.info(
            "provider_stub_chat",
            source=STUB_SOURCE,
            capability="chat",
            mode=step.mode,
            outcome=outcome,
            latency_ms=latency_ms,
        )


def _stub_reply(onboarding_step: int) -> str:
    index = min(max(onboarding_step, 0), len(ONBOARDING_STUB_REPLIES) - 1)
    return ONBOARDING_STUB_REPLIES[index]
