"""P13-T08 static review: scheduler stays out of API, clock ignores device time."""

from __future__ import annotations

import json
from inspect import getsource
from pathlib import Path

from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.domain.extract import MAX_EXTRACT_STYLE_SAMPLES
from app.main import _lifespan, create_app
from app.schemas.chat import ChatContext
from app.services import growth as growth_service
from app.services import quota as quota_service
from app.services import spirit_state as spirit_state_service
from app.services import state_scheduler as state_scheduler_service
from app.services import usage_scheduler as usage_scheduler_service

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _ROOT / "fixtures"
_MIGRATION_0009 = (
    _ROOT / "app" / "db" / "migrations" / "versions" / "20260908_0009_state_settle_scheduler.py"
)
_MIGRATION_0011 = _ROOT / "app" / "db" / "migrations" / "versions" / "20260908_0011_usage_rollup.py"
_OPENAPI_SHA = "fed40c13c30d2203f20ed83c7ad208042c13f35e330d3491e407f22be0593230"
_SETTLEMENT_MODULES = (
    growth_service,
    quota_service,
    spirit_state_service,
    state_scheduler_service,
    usage_scheduler_service,
)
_SCHEDULER_ENTRYPOINTS = (
    "scan_due_state_jobs",
    "process_due_state_ticks",
    "scan_usage_rollup_jobs",
    "process_usage_rollup_jobs",
    "enqueue_due_state_jobs",
)


def test_api_lifespan_does_not_start_scheduler() -> None:
    boot = getsource(create_app) + getsource(_lifespan)
    for name in _SCHEDULER_ENTRYPOINTS:
        assert name not in boot
    assert "kelin_scheduler" not in boot
    assert "BackgroundTasks" not in boot


def test_settlement_services_ignore_client_local_hour() -> None:
    description = ChatContext.__doc__ or ""
    assert "local_hour" in description
    assert "quota" in description
    for module in _SETTLEMENT_MODULES:
        source = getsource(module)
        assert "local_hour" not in source
        assert "datetime.now()" not in source


def test_enqueue_execute_is_granted_only_to_scheduler() -> None:
    state_sql = _MIGRATION_0009.read_text(encoding="utf-8")
    assert (
        "GRANT EXECUTE ON FUNCTION private.enqueue_due_state_jobs(timestamptz) TO kelin_scheduler"
        in state_sql
    )
    assert "enqueue_due_state_jobs(timestamptz) TO kelin_api" not in state_sql
    assert "enqueue_due_state_jobs(timestamptz) TO kelin_worker" not in state_sql
    usage_sql = _MIGRATION_0011.read_text(encoding="utf-8")
    assert "GRANT EXECUTE ON FUNCTION private.rollup_ai_usage(date) TO kelin_worker" in usage_sql
    assert "rollup_ai_usage(date) TO kelin_api" not in usage_sql
    assert "rollup_ai_usage(date) TO kelin_scheduler" not in usage_sql


def test_style_samples_stay_occasional_not_every_turn() -> None:
    assert MAX_EXTRACT_STYLE_SAMPLES == 1


def test_t08_does_not_change_openapi_sha() -> None:
    exported = export_openapi(create_app(Settings(app_env="test")))
    manifest = json.loads((_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert exported.sha256 == manifest["openapi_sha256"]
    assert exported.sha256 == _OPENAPI_SHA
