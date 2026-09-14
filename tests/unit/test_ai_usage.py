from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from app.core.config import Settings
from app.core.logging import configure_logging, hash_user_id
from app.domain.ai_usage import (
    DEFAULT_CRITICAL_PERCENT,
    DEFAULT_WARNING_PERCENT,
    FORBIDDEN_USAGE_PAYLOAD_KEYS,
    PROMPT_VERSIONS,
    BudgetPolicy,
    CostRates,
    UsageSample,
    assert_metadata_payload,
    budget_dedupe_key,
    budget_threshold,
    crossed_percent,
    estimate_cost_micros,
    prompt_version_for,
    require_capability,
    rollup_dedupe_key,
    utc_usage_date,
)
from app.repositories import ai_usage as usage_repo
from pydantic import ValidationError
from pytest import raises

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
RATES = CostRates(
    input_micros_per_unit=1,
    output_micros_per_unit=2,
    audio_micros_per_second=1000,
    image_micros_per_image=40,
)
POLICY = BudgetPolicy(daily_budget_micros=1000, warning_percent=70, critical_percent=100)


def test_prompt_versions_are_aliases_not_bodies() -> None:
    assert PROMPT_VERSIONS["chat"] == "chat/v3"
    assert prompt_version_for("extract") == "extract/v2"
    assert "你是" not in str(PROMPT_VERSIONS)
    assert "system" not in str(PROMPT_VERSIONS).lower()


def test_unknown_capability_is_rejected() -> None:
    with raises(ValueError, match="catalog"):
        require_capability("prompt")


def test_cost_is_estimated_in_micros() -> None:
    sample = UsageSample(
        request_id=uuid4(),
        capability="chat",
        success=True,
        input_units=10,
        output_units=5,
        audio_seconds=Decimal("1.5"),
        image_count=2,
    )
    assert estimate_cost_micros(sample, RATES) == 10 + 10 + 1500 + 80


def test_budget_thresholds_are_70_and_100() -> None:
    assert DEFAULT_WARNING_PERCENT == 70
    assert DEFAULT_CRITICAL_PERCENT == 100
    assert crossed_percent(spent_micros=700, budget_micros=1000, percent=70) is True
    assert crossed_percent(spent_micros=699, budget_micros=1000, percent=70) is False
    assert budget_threshold(spent_micros=700, policy=POLICY) == "warning"
    assert budget_threshold(spent_micros=1000, policy=POLICY) == "critical"
    assert budget_threshold(spent_micros=0, policy=POLICY) is None


def test_dedupe_keys_are_date_scoped() -> None:
    owner = uuid4()
    day = date(2026, 9, 8)
    assert rollup_dedupe_key(day) == "usage-rollup:2026-09-08"
    assert (
        budget_dedupe_key(owner_id=owner, usage_date=day, threshold="warning")
        == f"usage-budget-warning:{owner}:2026-09-08"
    )


def test_insert_sql_has_no_body_columns() -> None:
    sql = usage_repo.INSERT_SQL.lower()
    assert "prompt_version" in sql
    assert "model_alias" in sql
    assert "estimated_cost_micros" in sql
    for blocked in ("content", "reply", "body", "message", "summary", "audio", "image"):
        assert blocked not in sql.replace("image_count", "").replace("audio_seconds", "")
    assert "prompt," not in sql.replace("prompt_version", "")


def test_metadata_payload_rejects_prompt() -> None:
    with raises(ValueError, match="body"):
        assert_metadata_payload({"prompt": "你是刻灵", "usage_date": "2026-09-08"})


def test_utc_usage_date_uses_injected_clock() -> None:
    assert utc_usage_date(NOW) == date(2026, 9, 9)


def test_budget_config_stays_named_defaults() -> None:
    settings = Settings(app_env="test")
    assert settings.ai_daily_budget_micros == 10_000_000
    assert settings.ai_budget_warning_percent == 70
    assert settings.ai_budget_critical_percent == 100
    presence = settings.config_presence()
    assert presence["ai_daily_budget_micros"] == "10000000"
    assert "hard_stop" not in presence
    assert not hasattr(settings, "ai_budget_hard_stop")


def test_budget_config_rejects_non_positive() -> None:
    with raises(ValidationError):
        Settings(app_env="test", ai_daily_budget_micros=0)


def test_recorded_log_line_has_no_prompt_or_body() -> None:
    from app.core.logging import get_logger

    buf = StringIO()
    prompt = "系统提示：你是刻灵，请记住用户希望被叫阿年"
    with patch("sys.stdout", buf):
        configure_logging(Settings(app_env="test"))
        get_logger(component="ai_usage").info(
            "ai_usage.recorded",
            request_id=str(uuid4()),
            user_id_hash=hash_user_id(uuid4()),
            capability="chat",
            model_alias="chat",
            prompt_version="chat/v3",
            input_units=12,
            output_units=4,
            estimated_cost_micros=24,
            success=True,
        )
    text = buf.getvalue()
    assert "ai_usage.recorded" in text
    assert "chat/v3" in text
    assert prompt not in text
    assert "阿年" not in text
    assert "系统提示" not in text
    for key in FORBIDDEN_USAGE_PAYLOAD_KEYS:
        assert f'"{key}":' not in text
