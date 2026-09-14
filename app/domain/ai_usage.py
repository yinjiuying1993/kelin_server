"""Desensitized AI cost samples and budget alerts. Spec §§6.4, 16.6, 17.2, 18.1.

Named defaults only: prices and daily budget stay configurable. 100% never hard-stops.
Rows and logs store units/alias/cost, never Prompt or message body.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final, Literal
from uuid import UUID

from app.domain.spirit_state import require_aware

AiUsageCapability = Literal["chat", "extract", "asr", "tts", "vision", "safety", "search"]
BudgetThreshold = Literal["warning", "critical"]

AI_USAGE_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"chat", "extract", "asr", "tts", "vision", "safety", "search"}
)
PROMPT_VERSIONS: Final[dict[str, str]] = {
    "chat": "chat/v3",
    "extract": "extract/v2",
    "vision": "vision/v1",
    "safety": "safety/v1",
}
USAGE_ROLLUP_EVENT = "usage.rollup"
USAGE_BUDGET_WARNING_EVENT = "usage.budget.warning"
USAGE_BUDGET_CRITICAL_EVENT = "usage.budget.critical"
DEFAULT_DAILY_BUDGET_MICROS = 10_000_000
DEFAULT_WARNING_PERCENT = 70
DEFAULT_CRITICAL_PERCENT = 100
DEFAULT_INPUT_MICROS_PER_UNIT = 3
DEFAULT_OUTPUT_MICROS_PER_UNIT = 15
DEFAULT_AUDIO_MICROS_PER_SECOND = 1_000
DEFAULT_IMAGE_MICROS_PER_IMAGE = 40_000
USAGE_LOG_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "request_id",
        "user_id_hash",
        "capability",
        "model_alias",
        "prompt_version",
        "input_units",
        "output_units",
        "audio_seconds",
        "image_count",
        "estimated_cost_micros",
        "latency_ms",
        "success",
        "error_code",
        "provider_request_id",
        "usage_date",
        "spent_micros",
        "budget_micros",
        "threshold",
        "user_count",
        "request_count",
        "total_micros",
    }
)
FORBIDDEN_USAGE_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {
        "prompt",
        "content",
        "reply",
        "body",
        "message",
        "text",
        "summary",
        "audio",
        "image",
    }
)


@dataclass(frozen=True, slots=True)
class CostRates:
    input_micros_per_unit: int
    output_micros_per_unit: int
    audio_micros_per_second: int
    image_micros_per_image: int


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    daily_budget_micros: int
    warning_percent: int
    critical_percent: int


@dataclass(frozen=True, slots=True)
class UsageSample:
    request_id: UUID
    capability: str
    success: bool
    model_alias: str | None = None
    prompt_version: str | None = None
    input_units: int | None = None
    output_units: int | None = None
    audio_seconds: Decimal | None = None
    image_count: int | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class UsageRecord:
    request_id: UUID
    capability: str
    estimated_cost_micros: int
    usage_date: date
    spent_micros: int
    warning: bool
    critical: bool
    success: bool


@dataclass(frozen=True, slots=True)
class UsageRollup:
    usage_date: date
    user_count: int
    request_count: int
    total_micros: int
    alerts_emitted: int


def require_capability(capability: str) -> str:
    if capability not in AI_USAGE_CAPABILITIES:
        raise ValueError("ai_usage capability is not in the catalog")
    return capability


def utc_usage_date(now: datetime) -> date:
    require_aware(now, field="now")
    return now.astimezone(UTC).date()


def estimate_cost_micros(
    sample: UsageSample,
    rates: CostRates,
) -> int:
    audio = sample.audio_seconds if sample.audio_seconds is not None else Decimal("0")
    return (
        (sample.input_units or 0) * rates.input_micros_per_unit
        + (sample.output_units or 0) * rates.output_micros_per_unit
        + int(audio * rates.audio_micros_per_second)
        + (sample.image_count or 0) * rates.image_micros_per_image
    )


def crossed_percent(*, spent_micros: int, budget_micros: int, percent: int) -> bool:
    if budget_micros <= 0 or percent <= 0:
        return False
    return spent_micros * 100 >= budget_micros * percent


def budget_threshold(*, spent_micros: int, policy: BudgetPolicy) -> BudgetThreshold | None:
    if crossed_percent(
        spent_micros=spent_micros,
        budget_micros=policy.daily_budget_micros,
        percent=policy.critical_percent,
    ):
        return "critical"
    if crossed_percent(
        spent_micros=spent_micros,
        budget_micros=policy.daily_budget_micros,
        percent=policy.warning_percent,
    ):
        return "warning"
    return None


def rollup_dedupe_key(usage_date: date) -> str:
    return f"usage-rollup:{usage_date.isoformat()}"


def budget_dedupe_key(*, owner_id: UUID, usage_date: date, threshold: BudgetThreshold) -> str:
    return f"usage-budget-{threshold}:{owner_id}:{usage_date.isoformat()}"


def prompt_version_for(capability: str) -> str | None:
    return PROMPT_VERSIONS.get(require_capability(capability))


def assert_metadata_payload(payload: Mapping[str, object]) -> None:
    blocked = FORBIDDEN_USAGE_PAYLOAD_KEYS.intersection(payload)
    if blocked:
        raise ValueError("ai_usage payload must not include body fields")
