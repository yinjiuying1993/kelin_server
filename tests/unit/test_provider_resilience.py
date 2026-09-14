from __future__ import annotations

from app.providers.contract import PROVIDER_CONDITIONAL_RETRY_COUNT
from app.providers.resilience import (
    CapabilityCircuits,
    should_retry,
)


def test_search_does_not_retry_and_chat_retries_once() -> None:
    assert PROVIDER_CONDITIONAL_RETRY_COUNT == 1
    assert should_retry("http_5xx", capability="chat", retries_used=0, remaining_seconds=18) is True
    assert (
        should_retry("http_5xx", capability="chat", retries_used=1, remaining_seconds=18) is False
    )
    assert should_retry("timeout", capability="chat", retries_used=0, remaining_seconds=18) is False
    assert (
        should_retry("http_4xx", capability="chat", retries_used=0, remaining_seconds=18) is False
    )
    assert (
        should_retry("http_5xx", capability="search", retries_used=0, remaining_seconds=12) is False
    )
    assert (
        should_retry("connect", capability="chat", retries_used=0, remaining_seconds=0.01) is False
    )


def test_circuits_are_independent_and_need_error_rate_above_half() -> None:
    clock = {"t": 0.0}
    circuits = CapabilityCircuits(clock=lambda: clock["t"])
    circuits.record("chat", ok=False)
    assert circuits.is_open("chat") is False
    mixed = CapabilityCircuits()
    mixed.record("chat", ok=False)
    mixed.record("chat", ok=True)
    assert mixed.is_open("chat") is False
    circuits.record("chat", ok=False)
    assert circuits.is_open("chat") is True
    assert circuits.is_open("search") is False
    assert circuits.allow("chat") is False
    assert circuits.allow("search") is True
    clock["t"] = 120
    assert circuits.allow("chat") is True
    assert circuits.allow("chat") is False
    circuits.record("chat", ok=True)
    assert circuits.is_open("chat") is False
    assert circuits.allow("chat") is True
