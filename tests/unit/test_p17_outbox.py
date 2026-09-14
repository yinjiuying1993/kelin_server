"""P17-T02 generic outbox claim, lease, and fencing."""

from __future__ import annotations

from inspect import getsource
from pathlib import Path

from app.core.config import Settings
from app.domain.outbox import DEFAULT_OUTBOX_LEASE_SECONDS, retry_backoff_seconds
from app.main import _lifespan, create_app

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20260908_0017_p17_devices_outbox_notifications.py"
)


def test_lease_default_and_backoff() -> None:
    assert DEFAULT_OUTBOX_LEASE_SECONDS == 120
    assert Settings(app_env="test").outbox_lease_seconds == 120
    assert retry_backoff_seconds(1) == 2
    assert retry_backoff_seconds(8) == 256
    assert retry_backoff_seconds(9) == 300


def test_claim_sql_has_skip_locked_and_worker_only_grants() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "FOR UPDATE OF o SKIP LOCKED" in sql
    assert "private.claim_due_outbox(" in sql
    assert "private.renew_outbox_lease(" in sql
    assert "private.complete_outbox_job(" in sql
    assert "GRANT EXECUTE ON FUNCTION private.claim_due_outbox(" in sql
    assert "text[]) TO kelin_worker" in sql
    assert "claim_due_outbox(" not in "".join(
        line for line in sql.splitlines() if "kelin_api" in line and "GRANT EXECUTE" in line
    )
    assert "GRANT EXECUTE ON FUNCTION private.renew_outbox_lease" in sql
    assert "TO kelin_worker" in sql


def test_api_process_is_not_the_outbox_worker() -> None:
    boot = getsource(create_app) + getsource(_lifespan)
    assert "claim_due_outbox" not in boot
    assert "run_worker_tick" not in boot
    assert "process_due_notification_plans" not in boot
