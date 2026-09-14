from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from unittest.mock import patch

from app.core.config import Settings
from app.core.logging import configure_logging, hash_user_id
from app.core.security import CurrentUser
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.domain.ai_usage import UsageSample, budget_dedupe_key, rollup_dedupe_key, utc_usage_date
from app.services.ai_usage import record_ai_usage
from app.services.usage_scheduler import process_usage_rollup_jobs, scan_usage_rollup_jobs
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)
BUDGET = Settings(
    app_env="test",
    ai_daily_budget_micros=1000,
    ai_budget_warning_percent=70,
    ai_budget_critical_percent=100,
    ai_cost_input_micros_per_unit=1,
    ai_cost_output_micros_per_unit=1,
    ai_cost_audio_micros_per_second=0,
    ai_cost_image_micros_per_image=0,
)


async def _insert_auth_user(url: str, user_id: uuid.UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_ai_usage_cost_samples_alerts_and_rollup() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_ai_usage(url))


async def _assert_ai_usage(url: str) -> None:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    try:
        await _assert_success_and_failure_samples(url, factory)
        await _assert_budget_alerts_without_hard_stop(url, factory)
        await _assert_log_has_no_body(url, factory)
        await _assert_rollup_catchup(url, factory)
    finally:
        await engine.dispose()


async def _assert_success_and_failure_samples(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    success_id = uuid.uuid4()
    failure_id = uuid.uuid4()
    async with claimed_transaction(factory, user) as session:
        ok = await record_ai_usage(
            session,
            owner,
            UsageSample(
                request_id=success_id,
                capability="chat",
                success=True,
                model_alias="chat",
                prompt_version="chat/v3",
                input_units=20,
                output_units=8,
                provider_request_id="prov-ok",
                latency_ms=12,
            ),
            now=NOW,
            settings=BUDGET,
        )
        failed = await record_ai_usage(
            session,
            owner,
            UsageSample(
                request_id=failure_id,
                capability="extract",
                success=False,
                model_alias="extract",
                prompt_version="extract/v2",
                input_units=40,
                output_units=0,
                error_code="MODEL_UNAVAILABLE",
            ),
            now=NOW,
            settings=BUDGET,
        )
        rows = (
            await session.execute(
                text(
                    "SELECT request_id, capability, success, error_code, model_alias, "
                    "prompt_version, estimated_cost_micros "
                    "FROM public.ai_usage WHERE user_id = :user_id ORDER BY capability"
                ),
                {"user_id": owner},
            )
        ).all()
        columns = (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'ai_usage'"
                )
            )
        ).all()
    names = {str(row.column_name) for row in columns}
    assert "prompt" not in names
    assert "content" not in names
    assert ok.estimated_cost_micros == 28
    assert failed.estimated_cost_micros == 40
    assert failed.success is False
    assert [str(row.capability) for row in rows] == ["chat", "extract"]
    assert rows[0].success is True
    assert rows[1].success is False
    assert str(rows[1].error_code) == "MODEL_UNAVAILABLE"
    assert str(rows[0].model_alias) == "chat"
    assert str(rows[0].prompt_version) == "chat/v3"


async def _assert_budget_alerts_without_hard_stop(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        warning = await record_ai_usage(
            session,
            owner,
            UsageSample(
                request_id=uuid.uuid4(),
                capability="chat",
                success=True,
                input_units=700,
                output_units=0,
            ),
            now=NOW,
            settings=BUDGET,
        )
        critical = await record_ai_usage(
            session,
            owner,
            UsageSample(
                request_id=uuid.uuid4(),
                capability="chat",
                success=True,
                input_units=300,
                output_units=0,
            ),
            now=NOW,
            settings=BUDGET,
        )
        still_allowed = await record_ai_usage(
            session,
            owner,
            UsageSample(
                request_id=uuid.uuid4(),
                capability="chat",
                success=True,
                input_units=10,
                output_units=0,
            ),
            now=NOW,
            settings=BUDGET,
        )
        spent = await session.scalar(
            text(
                "SELECT COALESCE(SUM(estimated_cost_micros), 0) "
                "FROM public.ai_usage WHERE user_id = :user_id"
            ),
            {"user_id": owner},
        )
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        events = (
            await conn.execute(
                text(
                    "SELECT event_type, dedupe_key, payload "
                    "FROM public.outbox_events WHERE owner_id = :owner_id "
                    "ORDER BY event_type"
                ),
                {"owner_id": owner},
            )
        ).all()
    await engine.dispose()
    assert warning.warning is True
    assert warning.critical is False
    assert critical.critical is True
    assert still_allowed.critical is True
    assert still_allowed.estimated_cost_micros == 10
    assert int(spent or 0) == 1010
    types = [str(row.event_type) for row in events]
    assert types == ["usage.budget.critical", "usage.budget.warning"]
    usage_date = utc_usage_date(NOW)
    assert str(events[1].dedupe_key) == budget_dedupe_key(
        owner_id=owner, usage_date=usage_date, threshold="warning"
    )
    for row in events:
        payload = row.payload if isinstance(row.payload, dict) else json.loads(str(row.payload))
        assert set(payload) <= {
            "threshold",
            "usage_date",
            "estimated_cost_micros",
            "budget_micros",
        }
        blob = json.dumps(payload)
        assert "prompt" not in blob
        assert "content" not in blob


async def _assert_log_has_no_body(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    buf = StringIO()
    with patch("sys.stdout", buf):
        configure_logging(BUDGET)
        async with claimed_transaction(factory, user) as session:
            await record_ai_usage(
                session,
                owner,
                UsageSample(
                    request_id=uuid.uuid4(),
                    capability="chat",
                    success=True,
                    model_alias="chat",
                    prompt_version="chat/v3",
                    input_units=12,
                    output_units=3,
                    error_code=None,
                ),
                now=NOW,
                settings=BUDGET,
            )
    text_out = buf.getvalue()
    assert "ai_usage.recorded" in text_out
    assert hash_user_id(owner) in text_out
    assert str(owner) not in text_out
    assert "阿年" not in text_out
    assert "你是刻灵" not in text_out
    assert '"prompt":' not in text_out
    assert '"content":' not in text_out


async def _assert_rollup_catchup(url: str, factory: Any) -> None:
    owner = uuid.uuid4()
    await _insert_auth_user(url, owner)
    user = CurrentUser(id=owner)
    async with claimed_transaction(factory, user) as session:
        await record_ai_usage(
            session,
            owner,
            UsageSample(
                request_id=uuid.uuid4(),
                capability="tts",
                success=True,
                input_units=0,
                output_units=800,
            ),
            now=YESTERDAY,
            settings=BUDGET,
        )
    first = await scan_usage_rollup_jobs(factory, now=NOW)
    second = await scan_usage_rollup_jobs(factory, now=NOW)
    assert first == 1
    assert second == 0
    rollups = await process_usage_rollup_jobs(factory, now=NOW, settings=BUDGET)
    assert len(rollups) == 1
    assert rollups[0].usage_date == utc_usage_date(YESTERDAY)
    assert rollups[0].request_count == 1
    assert rollups[0].total_micros == 800
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        warning = await conn.scalar(
            text(
                "SELECT count(*) FROM public.outbox_events "
                "WHERE event_type = 'usage.budget.warning' AND owner_id = :owner_id"
            ),
            {"owner_id": owner},
        )
        rollup_status = await conn.scalar(
            text("SELECT status FROM public.outbox_events WHERE dedupe_key = :key"),
            {"key": rollup_dedupe_key(utc_usage_date(YESTERDAY))},
        )
    await engine.dispose()
    assert int(warning or 0) == 1
    assert str(rollup_status) == "done"
