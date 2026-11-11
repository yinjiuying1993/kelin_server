"""V3-S1-T02 ask and public-profile draft contract."""

from __future__ import annotations

import json

import pytest
from app.core.config import Settings
from app.main import create_app
from app.schemas.ask import (
    AskRecent,
    AskSubmitResult,
    HelpedResult,
    HelpedRow,
    PublicSkill,
    SpiritPublicProfile,
    points_for_rating,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError

_ASK_PATHS = (
    "/api/v1/ask/helped",
    "/api/v1/ask/questions",
    "/api/v1/ask/questions/recent",
    "/api/v1/ask/questions/{question_id}",
    "/api/v1/ask/questions/{question_id}/rating",
    "/api/v1/ask/questions/{question_id}/turns",
    "/api/v1/ops/craft-redeem",
    "/api/v1/spirits/{spirit_id}/public",
)
_PRIVATE = (
    "skill_id",
    "skillId",
    "keywords",
    "reference_links",
    "points_to_next",
    "total_points",
)


def test_rating_points_follow_the_three_tiers() -> None:
    assert points_for_rating("不满意") == 0
    assert points_for_rating("满意") == 5
    assert points_for_rating("非常满意") == 10


def test_unmatched_submit_has_no_dialogue() -> None:
    AskSubmitResult.model_validate({"matched": False, "question": None})
    with pytest.raises(ValidationError):
        AskSubmitResult.model_validate(
            {
                "matched": False,
                "question": {
                    "id": "00000000-0000-4000-8000-0000000000a1",
                    "question": "fixture",
                    "spirit_name": "fixture",
                    "rank_name": None,
                    "rated": False,
                    "turns": [{"role": "spirit", "text": "fixture"}],
                },
            }
        )


def test_public_and_helped_fields_stay_narrow() -> None:
    assert set(SpiritPublicProfile.model_fields) == {
        "id",
        "name",
        "display_name",
        "avatar_url",
        "help_count",
        "skills",
    }
    assert set(PublicSkill.model_fields) == {"title", "rank_name"}
    assert set(HelpedRow.model_fields) == {"spirit_name", "help_count"}
    assert set(HelpedResult.model_fields) == {"item"}
    AskRecent.model_validate({"items": []})
    with pytest.raises(ValidationError):
        HelpedRow.model_validate({"spirit_name": "fixture", "help_count": 0})
    with pytest.raises(ValidationError):
        SpiritPublicProfile.model_validate(
            {
                "id": "00000000-0000-4000-8000-0000000000a8",
                "name": "fixture",
                "help_count": 1,
                "skills": [],
                "points": 1,
            }
        )


def test_ask_openapi_hides_private_skill_fields() -> None:
    application = create_app(Settings(app_env="test"))
    document = application.openapi()
    for path in _ASK_PATHS:
        assert path in document["paths"]
    encoded = json.dumps({path: document["paths"][path] for path in _ASK_PATHS})
    for name in _PRIVATE:
        assert name not in encoded
    schemas = document["components"]["schemas"]
    assert set(schemas["HelpedRow"]["properties"]) == {"spirit_name", "help_count"}
    assert set(schemas["PublicSkill"]["properties"]) == {"title", "rank_name"}
    assert "question" not in schemas["SpiritPublicProfile"]["properties"]
    assert "points" not in schemas["SpiritPublicProfile"]["properties"]
    assert "question" not in schemas["HelpedResult"]["properties"]
    client = TestClient(application)
    response = client.post("/api/v1/ask/questions", json={})
    assert response.status_code == 401
    assert "skill_id" not in response.text
