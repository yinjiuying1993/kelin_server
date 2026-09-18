"""P16-T04 visit planner: 12h bucket, destination bound, catalog grants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from inspect import getsource, signature
from pathlib import Path
from uuid import uuid4

import pytest
from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.domain.visits import (
    MAX_VISIT_DESTINATIONS,
    NPC_FALLBACK_STAGE,
    VISIT_PLAN_EVENT,
    VISIT_PUBLIC_WEATHER,
    twelve_hour_bucket,
    visit_eligibility_key,
    visit_plan_dedupe_key,
)
from app.main import _lifespan, create_app
from app.services import visit_planner as visit_planner_service

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "app" / "db" / "migrations" / "versions" / "20260908_0014_visit_plan.py"
REPO = ROOT / "app" / "repositories" / "visits.py"
SERVICE = ROOT / "app" / "services" / "visit_planner.py"
ROUTER = ROOT / "app" / "api" / "v1" / "social.py"
_OPENAPI_SHA = "98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af"
NOW = datetime(2026, 9, 12, 11, 59, 59, tzinfo=UTC)
NEXT_BUCKET = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
SPIRIT = uuid4()


def test_twelve_hour_bucket_changes_at_utc_noon() -> None:
    assert twelve_hour_bucket(NOW) == "2026091200"
    assert twelve_hour_bucket(NOW.replace(hour=0, minute=0, second=0)) == "2026091200"
    assert twelve_hour_bucket(NEXT_BUCKET) == "2026091212"
    assert twelve_hour_bucket(NEXT_BUCKET + timedelta(hours=11, minutes=59)) == "2026091212"
    assert twelve_hour_bucket(NOW) != twelve_hour_bucket(NEXT_BUCKET)


def test_eligibility_and_dedupe_keys_are_per_spirit_bucket() -> None:
    assert visit_plan_dedupe_key(SPIRIT, now=NOW) == f"visit-plan:{SPIRIT}:2026091200"
    assert visit_plan_dedupe_key(SPIRIT, now=NEXT_BUCKET) == f"visit-plan:{SPIRIT}:2026091212"
    assert visit_eligibility_key(SPIRIT, now=NOW, destination_index=1) == (
        f"visit:{SPIRIT}:2026091200:1"
    )
    assert visit_eligibility_key(SPIRIT, now=NOW, destination_index=2).endswith(":2")
    with pytest.raises(ValueError):
        visit_eligibility_key(SPIRIT, now=NOW, destination_index=3)
    assert MAX_VISIT_DESTINATIONS == 2
    assert VISIT_PLAN_EVENT == "visit.plan"
    assert NPC_FALLBACK_STAGE == "formed"
    assert VISIT_PUBLIC_WEATHER == "cloudy"
    assert SERVICE.exists()


def test_planner_catalog_has_no_host_user_npc_arguments() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "private.plan_visits(p_now timestamptz)" in sql
    assert "private.enqueue_due_visit_plan_jobs(p_now timestamptz)" in sql
    assert "p_user_id" not in sql
    assert "p_host" not in sql
    assert "p_npc" not in sql
    assert "GRANT EXECUTE ON FUNCTION private.plan_visits(timestamptz) TO kelin_worker" in sql
    assert "plan_visits(timestamptz) TO kelin_api" not in sql
    assert "plan_visits(timestamptz) TO kelin_scheduler" not in sql
    assert (
        "GRANT EXECUTE ON FUNCTION private.enqueue_due_visit_plan_jobs(timestamptz) "
        "TO kelin_scheduler"
    ) in sql
    assert "enqueue_due_visit_plan_jobs(timestamptz) TO kelin_api" not in sql
    assert "enqueue_due_visit_plan_jobs(timestamptz) TO kelin_worker" not in sql
    assert "SET search_path = pg_catalog, public, private" in sql
    assert "SECURITY DEFINER" in sql
    assert "destination_index" in sql
    assert "LIMIT 2" in sql
    assert "visit_on" in sql
    assert "npc_profiles" in sql
    repo = REPO.read_text(encoding="utf-8")
    assert "SELECT private.plan_visits(:now)" in repo
    assert "SELECT private.enqueue_due_visit_plan_jobs(:now)" in repo
    planner_binds = repo.replace(":host_text", "").replace(":visitor_text", "")
    assert ":host" not in planner_binds
    assert ":npc" not in planner_binds
    assert ":user_id" not in planner_binds


def test_worker_handle_does_not_accept_host_or_npc() -> None:
    names = set(signature(visit_planner_service.handle_visit_plan).parameters)
    assert names == {"factory", "now"}
    names = set(signature(visit_planner_service.plan_due_visits).parameters)
    assert names == {"session", "now"}
    source = getsource(visit_planner_service)
    assert "host_spirit_id" not in source
    assert "npc_id" not in source
    assert "datetime.now()" not in source


def test_api_lifespan_does_not_start_visit_planner() -> None:
    boot = getsource(create_app) + getsource(_lifespan)
    assert "visit_planner" not in boot
    assert "handle_visit_plan" not in boot
    assert "plan_due_visits" not in boot
    router = ROUTER.read_text(encoding="utf-8")
    assert "handle_visit_plan" not in router
    assert "plan_due_visits" not in router
    assert "POST /visits" not in router


def test_t04_does_not_change_openapi_sha() -> None:
    exported = export_openapi(create_app(Settings(app_env="test")))
    assert exported.sha256 == _OPENAPI_SHA
