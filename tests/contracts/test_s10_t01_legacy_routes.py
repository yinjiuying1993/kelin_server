"""S10-T01: removed public entries do not settle."""

from __future__ import annotations

from inspect import getsource
from uuid import uuid4

from app.core.config import Settings
from app.main import create_app
from app.schemas.recall import RecallRequest
from app.services import cron as cron_service
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

_GONE = (
    "/api/v1/moderate-sight",
    "/api/v1/storage/sight-upload-url",
    "/api/v1/devices",
    "/api/v1/onboarding/complete",
    "/api/v1/feed",
    "/api/v1/notifications",
    "/api/v1/notification-plan",
)


def test_legacy_entries_are_absent_and_do_not_settle() -> None:
    application = create_app(Settings(app_env="test"))
    paths = set(application.openapi()["paths"])
    for path in _GONE:
        assert path not in paths
    assert not any("notification" in path for path in paths)

    client = TestClient(application)
    for path in _GONE:
        response = client.post(path, json={"client_id": str(uuid4())})
        assert response.status_code == 404
        body = response.json()
        assert body["ok"] is False
        assert body["data"] is None
        assert body["error"]["code"] == "NOT_FOUND"

    try:
        RecallRequest.model_validate(
            {"client_id": str(uuid4()), "method": "sight_memory", "memory_id": str(uuid4())}
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("sight_memory recall must not validate")

    recall = client.post(
        "/api/v1/recall",
        json={"client_id": str(uuid4()), "method": "sight_memory", "memory_id": str(uuid4())},
    )
    assert recall.status_code in {401, 422}
    assert recall.json()["ok"] is False
    assert recall.json()["data"] is None

    ticks = getsource(cron_service.run_scheduler_tick) + getsource(cron_service.run_worker_tick)
    assert "scan_due_notification_plan_jobs" not in ticks
    assert "process_due_notification_plans" not in ticks
