"""S3-T05: visit reads settle the target spirit through central."""

from __future__ import annotations

from inspect import getsource
from pathlib import Path
from uuid import uuid4

from app.domain.central import OrchestratorRequest, plan
from app.domain.central.pipeline import BOOTSTRAP_PIPELINE
from app.services.central.pipelines import bind_pipeline

ROOT = Path(__file__).resolve().parents[2]
VISIT_TARGET = ROOT / "app" / "services" / "central" / "visit_target.py"
VISIT_SETTLE = ROOT / "app" / "services" / "visit_settle.py"
FRIENDS_ROUTER = ROOT / "app" / "api" / "v1" / "social.py"


def test_visit_target_uses_clock_tick_bootstrap_pipeline() -> None:
    from datetime import UTC, datetime

    request = OrchestratorRequest(
        request_kind="clock_tick",
        event_id=uuid4(),
        spirit_id=uuid4(),
        occurred_at=datetime(2026, 9, 20, 12, tzinfo=UTC),
        client_payload_hash="b" * 64,
        current_snapshot_version=1,
    )
    bound = bind_pipeline(request, entry="bootstrap")
    assert bound.pipeline == BOOTSTRAP_PIPELINE
    assert plan(request).reason_code == "PLAN_CLOCK_TICK"


def test_visit_read_path_settles_target_through_central() -> None:
    target = VISIT_TARGET.read_text(encoding="utf-8")
    settle = VISIT_SETTLE.read_text(encoding="utf-8")
    router = FRIENDS_ROUTER.read_text(encoding="utf-8")
    assert "request_kind=\"clock_tick\"" in target or 'request_kind="clock_tick"' in target
    assert "run_bootstrap" in target
    assert "settle_then_resolve" in target
    assert "load_bootstrap_snapshot" not in target
    assert "datetime.now()" not in target
    assert "host_spirit_id" not in settle
    assert "settle_visit_spirit_targets" in settle
    assert "public.host_id" in settle
    assert "load_settled_friend_page" in router
    assert "timedelta(hours=18)" not in target
    assert "timedelta(hours=72)" not in target
    assert "desired_spirit_state" not in target
    from app.services import visit_settle as visit_settle_service

    source = getsource(visit_settle_service)
    assert "host_spirit_id" not in source
