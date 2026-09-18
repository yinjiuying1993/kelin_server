"""P16-T05 visit settle: lease, one-shot transition, no client host/NPC."""

from __future__ import annotations

from inspect import getsource, signature
from pathlib import Path
from uuid import uuid4

from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.domain.growth import growth_catalog_by_event
from app.domain.visits import (
    DEFAULT_OUTBOX_LEASE_SECONDS,
    VISIT_SETTLE_EVENT,
    visit_settle_dedupe_key,
    visit_template_text,
)
from app.main import _lifespan, create_app
from app.services import visit_settle as visit_settle_service

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "app" / "db" / "migrations" / "versions" / "20260908_0015_visit_settle.py"
REPO = ROOT / "app" / "repositories" / "visits.py"
SERVICE = ROOT / "app" / "services" / "visit_settle.py"
ROUTER = ROOT / "app" / "api" / "v1" / "social.py"
_OPENAPI_SHA = "98d1c74287949966df83779e5188f90eb408ec50bef6ee472ea707472fbdc0af"


def test_settle_keys_and_templates_are_public_only() -> None:
    visit_id = uuid4()
    assert visit_settle_dedupe_key(visit_id) == f"visit-settle:{visit_id}"
    assert VISIT_SETTLE_EVENT == "visit.settle"
    assert DEFAULT_OUTBOX_LEASE_SECONDS == 120
    visitor = visit_template_text(title="甲居", for_host=False)
    host = visit_template_text(title="甲居", for_host=True)
    assert "甲居" in visitor
    assert "user_id" not in visitor and "记忆" not in visitor
    assert "对话" not in host and "坐标" not in host
    catalog = growth_catalog_by_event()["visit_completed"]
    assert catalog.producer_status == "wired"
    assert catalog.source_type == "visit"


def test_settle_catalog_has_no_host_user_npc_arguments() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "private.settle_visit(p_visit_id uuid, p_now timestamptz)" in sql
    assert "p_user_id" not in sql
    assert "p_host" not in sql
    assert "p_npc" not in sql
    assert "GRANT EXECUTE ON FUNCTION private.settle_visit(uuid, timestamptz) TO kelin_worker" in sql
    assert "settle_visit(uuid, timestamptz) TO kelin_api" not in sql
    assert "enqueue_due_visit_settle_jobs(timestamptz) TO kelin_api" not in sql
    assert (
        "GRANT EXECUTE ON FUNCTION private.enqueue_due_visit_settle_jobs(timestamptz) TO kelin_scheduler"
    ) in sql
    assert "SKIP LOCKED" in sql
    assert "lease_expires_at" in sql
    assert "visit-settle:" in sql
    repo = REPO.read_text(encoding="utf-8")
    assert "SELECT private.settle_visit(:visit_id, :now, :visitor_text, :host_text)" in repo
    assert ":host_spirit_id" not in repo
    assert ":npc_id" not in repo
    names = set(signature(visit_settle_service.handle_visit_settle).parameters)
    assert names == {"factory", "visit_id", "now"}
    source = getsource(visit_settle_service)
    assert "datetime.now()" not in source
    assert "host_spirit_id" not in source


def test_api_does_not_start_visit_settle_or_expose_create() -> None:
    boot = getsource(create_app) + getsource(_lifespan)
    assert "visit_settle" not in boot
    assert "process_due_visit_settles" not in boot
    assert "scan_due_visit_settle_jobs" not in boot
    router = ROUTER.read_text(encoding="utf-8")
    assert "handle_visit_settle" not in router
    assert "settle_visit" not in router
    exported = export_openapi(create_app(Settings(app_env="test")))
    assert exported.sha256 == _OPENAPI_SHA
    assert SERVICE.exists()
