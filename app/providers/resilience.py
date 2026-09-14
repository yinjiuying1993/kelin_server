"""Per-capability retry and circuit breaker. Spec §16.5."""

from __future__ import annotations

import random
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic

from app.providers.contract import (
    PROVIDER_CIRCUIT_ERROR_RATE,
    PROVIDER_CIRCUIT_MIN_SAMPLES,
    PROVIDER_CIRCUIT_OPEN_SECONDS,
    PROVIDER_CIRCUIT_WINDOW_SECONDS,
    PROVIDER_CONDITIONAL_RETRY_COUNT,
    PROVIDER_RETRY_ON,
    ProviderCapability,
)

RETRYABLE_KINDS = PROVIDER_RETRY_ON
SEARCH_RETRY_ON: frozenset[str] = frozenset()
_RETRY_BASE_SECONDS = 0.05
HALF_OPEN_MAX_PROBES = 1
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class CircuitSample:
    at: float
    ok: bool


@dataclass
class _CapabilityState:
    samples: deque[CircuitSample] = field(default_factory=deque)
    open_until: float | None = None
    half_open_inflight: int = 0


class CapabilityCircuits:
    """Independent circuits. Open after window error rate >50% with enough samples."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        window_seconds: float = PROVIDER_CIRCUIT_WINDOW_SECONDS,
        open_seconds: float = PROVIDER_CIRCUIT_OPEN_SECONDS,
        min_samples: int = PROVIDER_CIRCUIT_MIN_SAMPLES,
        error_rate: float = PROVIDER_CIRCUIT_ERROR_RATE,
        half_open_max: int = HALF_OPEN_MAX_PROBES,
    ) -> None:
        self._clock = clock or monotonic
        self._window_seconds = window_seconds
        self._open_seconds = open_seconds
        self._min_samples = min_samples
        self._error_rate = error_rate
        self._half_open_max = half_open_max
        self._states: dict[str, _CapabilityState] = defaultdict(_CapabilityState)

    def allow(self, capability: ProviderCapability) -> bool:
        state = self._states[capability]
        now = self._clock()
        self._prune(state, now)
        if state.open_until is None:
            return True
        if now < state.open_until:
            return False
        if state.half_open_inflight >= self._half_open_max:
            return False
        state.half_open_inflight += 1
        return True

    def record(self, capability: ProviderCapability, *, ok: bool) -> None:
        state = self._states[capability]
        now = self._clock()
        state.samples.append(CircuitSample(at=now, ok=ok))
        self._prune(state, now)
        half_open = state.half_open_inflight > 0
        if half_open:
            state.half_open_inflight = max(0, state.half_open_inflight - 1)
            if ok:
                state.open_until = None
                state.half_open_inflight = 0
            else:
                state.open_until = now + self._open_seconds
            return
        if not ok and _error_rate(state.samples) > self._error_rate:
            if len(state.samples) >= self._min_samples:
                state.open_until = now + self._open_seconds

    def is_open(self, capability: ProviderCapability) -> bool:
        state = self._states[capability]
        now = self._clock()
        return state.open_until is not None and now < state.open_until

    def _prune(self, state: _CapabilityState, now: float) -> None:
        cutoff = now - self._window_seconds
        while state.samples and state.samples[0].at < cutoff:
            state.samples.popleft()


def _error_rate(samples: deque[CircuitSample]) -> float:
    if not samples:
        return 0.0
    failures = sum(1 for sample in samples if not sample.ok)
    return failures / len(samples)


def retry_delay_seconds(retry_number: int, *, rng: random.Random | None = None) -> float:
    generator = rng if rng is not None else random.Random()
    expo = _RETRY_BASE_SECONDS * (2 ** max(0, retry_number - 1))
    return float(expo + generator.uniform(0, _RETRY_BASE_SECONDS))


def should_retry(
    kind: str,
    *,
    capability: ProviderCapability,
    retries_used: int,
    remaining_seconds: float,
    rng: random.Random | None = None,
) -> bool:
    allowed = SEARCH_RETRY_ON if capability == "search" else RETRYABLE_KINDS
    if kind not in allowed:
        return False
    if retries_used >= PROVIDER_CONDITIONAL_RETRY_COUNT:
        return False
    delay = retry_delay_seconds(retries_used + 1, rng=rng)
    return remaining_seconds > delay + 0.05


_DEFAULT_CIRCUITS = CapabilityCircuits()


def default_circuits() -> CapabilityCircuits:
    return _DEFAULT_CIRCUITS


def reset_default_circuits() -> None:
    global _DEFAULT_CIRCUITS
    _DEFAULT_CIRCUITS = CapabilityCircuits()
