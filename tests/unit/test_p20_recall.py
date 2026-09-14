"""P20 recall contract: two methods, no client growth, OpenAPI surface."""

from __future__ import annotations

from uuid import uuid4

from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.main import create_app
from app.schemas.recall import RecallRequest
from pydantic import TypeAdapter, ValidationError
from pytest import raises

_ADAPTER = TypeAdapter(RecallRequest)


def test_recall_request_food_and_sight_shapes() -> None:
    client = str(uuid4())
    food = _ADAPTER.validate_python({"client_id": client, "method": "food"})
    assert food.method == "food"
    memory_id = str(uuid4())
    sight = _ADAPTER.validate_python(
        {"client_id": client, "method": "sight_memory", "memory_id": memory_id}
    )
    assert sight.method == "sight_memory"
    assert str(sight.memory_id) == memory_id


def test_recall_request_rejects_growth_and_illegal_source() -> None:
    client = str(uuid4())
    with raises(ValidationError):
        _ADAPTER.validate_python({"method": "food"})
    with raises(ValidationError):
        _ADAPTER.validate_python({"client_id": client, "method": "sight_memory"})
    with raises(ValidationError):
        _ADAPTER.validate_python({"client_id": client, "method": "pay"})
    with raises(ValidationError):
        _ADAPTER.validate_python({"client_id": client, "method": "food", "hunger_delta": 10})
    with raises(ValidationError):
        _ADAPTER.validate_python({"client_id": client, "method": "food", "bond": 5})


def test_openapi_registers_recall_not_debug() -> None:
    paths = export_openapi(create_app(Settings(app_env="test"))).document["paths"]
    assert "post" in paths["/api/v1/recall"]
    assert not any(path.startswith("/api/v1/debug") for path in paths)
