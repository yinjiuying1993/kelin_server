"""Record desensitized AI cost and emit 70/100 budget alerts. Spec §§16.6, 18.4.

Does not hard-stop at 100%. Prompt and message body never enter SQL or logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, hash_user_id
from app.domain.ai_usage import (
    USAGE_LOG_FIELDS,
    BudgetPolicy,
    CostRates,
    UsageRecord,
    UsageSample,
    crossed_percent,
    estimate_cost_micros,
    require_capability,
    utc_usage_date,
)
from app.domain.spirit_state import require_aware
from app.repositories import ai_usage as usage_repo

_LOGGER = get_logger(component="ai_usage")


def cost_rates(settings: Settings) -> CostRates:
    return CostRates(
        input_micros_per_unit=settings.ai_cost_input_micros_per_unit,
        output_micros_per_unit=settings.ai_cost_output_micros_per_unit,
        audio_micros_per_second=settings.ai_cost_audio_micros_per_second,
        image_micros_per_image=settings.ai_cost_image_micros_per_image,
    )


def budget_policy(settings: Settings) -> BudgetPolicy:
    return BudgetPolicy(
        daily_budget_micros=settings.ai_daily_budget_micros,
        warning_percent=settings.ai_budget_warning_percent,
        critical_percent=settings.ai_budget_critical_percent,
    )


def _log(event: str, fields: dict[str, object]) -> None:
    safe = {key: value for key, value in fields.items() if key in USAGE_LOG_FIELDS}
    _LOGGER.info(event, **safe)


async def record_ai_usage(
    session: AsyncSession,
    owner_id: uuid.UUID,
    sample: UsageSample,
    *,
    now: datetime,
    settings: Settings | None = None,
) -> UsageRecord:
    require_aware(now, field="now")
    cap = require_capability(sample.capability)
    await usage_repo.assert_writable_transaction(session)
    resolved = settings or get_settings()
    rates = cost_rates(resolved)
    policy = budget_policy(resolved)
    cost = estimate_cost_micros(sample, rates)
    await usage_repo.insert_usage(
        session,
        owner_id,
        UsageSample(
            request_id=sample.request_id,
            capability=cap,
            success=sample.success,
            model_alias=sample.model_alias,
            prompt_version=sample.prompt_version,
            input_units=sample.input_units,
            output_units=sample.output_units,
            audio_seconds=sample.audio_seconds,
            image_count=sample.image_count,
            latency_ms=sample.latency_ms,
            error_code=sample.error_code,
            provider_request_id=sample.provider_request_id,
        ),
        estimated_cost_micros=cost,
        now=now,
    )
    usage_date = utc_usage_date(now)
    spent = await usage_repo.day_spent_micros(session, owner_id, usage_date=usage_date)
    warning = crossed_percent(
        spent_micros=spent,
        budget_micros=policy.daily_budget_micros,
        percent=policy.warning_percent,
    )
    critical = crossed_percent(
        spent_micros=spent,
        budget_micros=policy.daily_budget_micros,
        percent=policy.critical_percent,
    )
    _log(
        "ai_usage.recorded",
        {
            "request_id": str(sample.request_id),
            "user_id_hash": hash_user_id(owner_id),
            "capability": cap,
            "model_alias": sample.model_alias,
            "prompt_version": sample.prompt_version,
            "input_units": sample.input_units,
            "output_units": sample.output_units,
            "audio_seconds": (
                str(sample.audio_seconds) if sample.audio_seconds is not None else None
            ),
            "image_count": sample.image_count,
            "estimated_cost_micros": cost,
            "latency_ms": sample.latency_ms,
            "success": sample.success,
            "error_code": sample.error_code,
            "provider_request_id": sample.provider_request_id,
            "usage_date": usage_date.isoformat(),
            "spent_micros": spent,
            "budget_micros": policy.daily_budget_micros,
        },
    )
    if warning:
        inserted = await usage_repo.insert_budget_alert(
            session,
            owner_id,
            usage_date=usage_date,
            threshold="warning",
            spent_micros=spent,
            budget_micros=policy.daily_budget_micros,
            now=now,
        )
        if inserted:
            _log(
                "ai_usage.budget.warning",
                {
                    "user_id_hash": hash_user_id(owner_id),
                    "usage_date": usage_date.isoformat(),
                    "spent_micros": spent,
                    "budget_micros": policy.daily_budget_micros,
                    "threshold": "warning",
                },
            )
    if critical:
        inserted = await usage_repo.insert_budget_alert(
            session,
            owner_id,
            usage_date=usage_date,
            threshold="critical",
            spent_micros=spent,
            budget_micros=policy.daily_budget_micros,
            now=now,
        )
        if inserted:
            _log(
                "ai_usage.budget.critical",
                {
                    "user_id_hash": hash_user_id(owner_id),
                    "usage_date": usage_date.isoformat(),
                    "spent_micros": spent,
                    "budget_micros": policy.daily_budget_micros,
                    "threshold": "critical",
                },
            )
    return UsageRecord(
        request_id=sample.request_id,
        capability=cap,
        estimated_cost_micros=cost,
        usage_date=usage_date,
        spent_micros=spent,
        warning=warning,
        critical=critical,
        success=sample.success,
    )
