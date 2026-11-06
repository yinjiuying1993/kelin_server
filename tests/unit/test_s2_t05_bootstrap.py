"""S2-T05: GET /bootstrap goes through facade; bootstrap does not write mood/stage."""

from __future__ import annotations

from pathlib import Path

from app.domain.central import OrchestratorRequest, plan
from app.domain.central.pipeline import BOOTSTRAP_PIPELINE
from app.services.central.pipelines import bind_pipeline

ROUTER = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "bootstrap.py"
SERVICE = Path(__file__).resolve().parents[2] / "app" / "services" / "bootstrap.py"


def test_bootstrap_request_binds_bootstrap_pipeline() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    request = OrchestratorRequest(
        request_kind="bootstrap",
        event_id=uuid4(),
        spirit_id=uuid4(),
        occurred_at=datetime(2026, 9, 20, 12, tzinfo=UTC),
        client_payload_hash="a" * 64,
        current_snapshot_version=1,
    )
    bound = bind_pipeline(request, entry="bootstrap")
    assert bound.pipeline == BOOTSTRAP_PIPELINE
    assert "idempotency" not in bound.pipeline
    assert plan(request).reason_code == "PLAN_BOOTSTRAP"


def test_router_is_thin_facade_forward() -> None:
    source = ROUTER.read_text(encoding="utf-8")
    assert "run_bootstrap_snapshot" in source
    assert "claimed_transaction" in source
    assert "claimed_read_transaction" not in source
    assert "settle_spirit_state" not in source
    assert "mood" not in source
    assert "stage =" not in source


def test_bootstrap_service_does_not_write_mood_or_stage() -> None:
    source = SERVICE.read_text(encoding="utf-8")
    assert "run_bootstrap" in source
    assert "settle_then_resolve" in source
    assert "desired_spirit_state_at" not in source
    assert "maybe_advance_stage" not in source
    assert "mood =" not in source
    assert "SET stage" not in source
    assert "stage =" not in source
