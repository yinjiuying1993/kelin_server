"""V3-S1-T01 craft and self-test draft contract."""

from __future__ import annotations

import json

from app.core.config import Settings
from app.main import create_app
from app.schemas.craft import rank_for_points
from fastapi.testclient import TestClient

_CRAFT_PATHS = (
    "/api/v1/craft/redeem",
    "/api/v1/craft/self-test/turns",
    "/api/v1/craft/skills",
    "/api/v1/craft/skills/{craft_id}",
    "/api/v1/craft/skills/{craft_id}/self-test",
    "/api/v1/craft/skills/{craft_id}/visibility",
)


def test_rank_gaps_follow_the_point_table() -> None:
    assert rank_for_points(0).name is None
    assert rank_for_points(0).points_to_next == 1
    assert rank_for_points(1).name == "门边学徒"
    assert rank_for_points(99).points_to_next == 1
    assert rank_for_points(100).name == "搭手帮工"
    assert rank_for_points(299).points_to_next == 1
    assert rank_for_points(300).name == "上手匠人"
    assert rank_for_points(599).points_to_next == 1
    assert rank_for_points(600).name == "独当师傅"
    assert rank_for_points(999).points_to_next == 1
    assert rank_for_points(1000).name == "掌作名匠"
    assert rank_for_points(1499).points_to_next == 1
    top = rank_for_points(1500)
    assert top.name == "镇场宗师"
    assert top.points_to_next is None
    assert top.at_max_rank is True
    assert rank_for_points(2000) == top


def test_craft_openapi_uses_resource_ids_only() -> None:
    application = create_app(Settings(app_env="test"))
    document = application.openapi()
    paths = document["paths"]
    for path in _CRAFT_PATHS:
        assert path in paths
    encoded = json.dumps(document)
    assert "skill_id" not in encoded
    assert "skillId" not in encoded
    client = TestClient(application)
    response = client.get("/api/v1/craft/skills")
    assert response.status_code == 401
    assert "skill_id" not in response.text
