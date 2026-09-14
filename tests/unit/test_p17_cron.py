"""P17-T05 independent scheduler/worker commands."""

from __future__ import annotations

from inspect import getsource
from pathlib import Path

from app.main import _lifespan, create_app
from app.scheduler import main as scheduler_main
from app.worker import main as worker_main

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
DOCKERFILE = ROOT / "Dockerfile"


def test_compose_and_dockerfile_keep_api_scheduler_worker_separate() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert '"-m", "app.scheduler"' in compose or "python -m app.scheduler" in compose
    assert '"-m", "app.worker"' in compose or "python -m app.worker" in compose
    assert "uvicorn" in dockerfile
    assert "app.scheduler" not in dockerfile
    assert "app.worker" not in dockerfile
    assert compose.index("app.scheduler") != compose.index("app.worker")


def test_api_startup_does_not_start_cron() -> None:
    boot = getsource(create_app) + getsource(_lifespan)
    assert "run_scheduler_tick" not in boot
    assert "run_worker_tick" not in boot
    assert "scheduler_main" not in boot
    assert "worker_main" not in boot
    assert "--once" in getsource(scheduler_main)
    assert "--once" in getsource(worker_main)
