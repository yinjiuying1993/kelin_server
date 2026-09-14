"""P18 report rules, snapshot validators, and OpenAPI surface."""

from __future__ import annotations

from inspect import getsource
from pathlib import Path
from uuid import UUID

from app.contracts.openapi import export_openapi
from app.core.config import Settings
from app.domain.report import (
    FALLBACK_TITLE,
    REPORT_RULES_VERSION,
    STUB_SIGNATURE_LINE,
    stable_card_fields,
    stable_card_hash,
    title_from_traits,
    top_traits_from_values,
)
from app.main import create_app
from app.schemas.report import ReportCardModel, ReportSnapshot
from pydantic import ValidationError
from pytest import raises

REPORT_ID = "00000000-0000-4000-8000-0000000000c1"
SPIRIT_ID = "00000000-0000-4000-8000-0000000000b1"
MEM_A = "00000000-0000-4000-8000-0000000000d1"
MEM_B = "00000000-0000-4000-8000-0000000000d2"


def _eligibility(*, eligible: bool = False) -> dict[str, object]:
    return {
        "is_eligible": eligible,
        "eligible_at": "2026-09-14T03:46:00Z",
        "days_remaining": 0 if eligible else 7,
        "required_dialogue_rounds": 10,
        "completed_dialogue_rounds": 10 if eligible else 0,
        "dialogue_rounds_remaining": 0 if eligible else 10,
    }


def _card(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": REPORT_ID,
        "status": "partial",
        "rules_version": REPORT_RULES_VERSION,
        "title": "阴天收集者 · 学者",
        "spirit": {
            "id": SPIRIT_ID,
            "name": "雾生",
            "stage": "formed",
            "public_layers": ["spirit", "scholar"],
        },
        "room_weather": "cloudy",
        "top_traits": [
            {"dimension": "closeness", "value": 78},
            {"dimension": "nocturnal", "value": 66},
        ],
        "top_memories": [
            {"id": MEM_A, "type": "sight", "summary": "雨夜路灯", "unavailable": False},
            {"id": MEM_B, "type": None, "summary": None, "unavailable": True},
        ],
        "scholar_marks": ["interview-v1"],
        "signature_line": None,
        "invite_code": "ABCD2345",
        "generated_at": "2026-09-14T04:00:00Z",
        "version": 2,
    }
    payload.update(overrides)
    return payload


def test_title_rules_match_prd_and_scholar_suffix() -> None:
    assert title_from_traits(closeness=30, sharpness=70, nocturnal=70, scholar_marks=()) == "夜行毒舌"
    assert (
        title_from_traits(closeness=70, sharpness=70, nocturnal=30, scholar_marks=())
        == "会翻旧账的暖炉"
    )
    assert (
        title_from_traits(closeness=70, sharpness=30, nocturnal=70, scholar_marks=("interview-v1",))
        == "阴天收集者 · 学者"
    )
    assert title_from_traits(closeness=70, sharpness=30, nocturnal=30, scholar_marks=()) == "温石头"
    assert (
        title_from_traits(closeness=70, sharpness=70, nocturnal=70, scholar_marks=()) == "守夜的损友"
    )
    assert title_from_traits(closeness=30, sharpness=30, nocturnal=70, scholar_marks=()) == "雾里旁观"
    assert title_from_traits(closeness=30, sharpness=70, nocturnal=30, scholar_marks=()) == "白日锋利"
    assert (
        title_from_traits(closeness=50, sharpness=50, nocturnal=50, scholar_marks=())
        == "守夜的损友"
    )
    assert (
        title_from_traits(closeness=40, sharpness=40, nocturnal=40, scholar_marks=())
        == FALLBACK_TITLE
    )


def test_top_traits_are_highest_two_with_stable_ties() -> None:
    traits = top_traits_from_values(
        {
            "closeness": 80,
            "curiosity": 80,
            "sharpness": 10,
            "nocturnal": 10,
            "stubborn": 10,
        }
    )
    assert traits[0]["dimension"] == "closeness"
    assert traits[1]["dimension"] == "curiosity"


def test_locked_and_partial_snapshot_rules() -> None:
    locked = ReportSnapshot.model_validate(
        {
            "status": "locked",
            "report_id": None,
            "eligibility": _eligibility(),
            "card": None,
        }
    )
    assert locked.card is None
    with raises(ValidationError):
        ReportSnapshot.model_validate(
            {
                "status": "locked",
                "report_id": REPORT_ID,
                "eligibility": _eligibility(),
                "card": None,
            }
        )
    partial = ReportSnapshot.model_validate(
        {
            "status": "partial",
            "report_id": REPORT_ID,
            "eligibility": _eligibility(eligible=True),
            "card": _card(),
        }
    )
    assert partial.card is not None
    assert partial.card.top_memories[1].unavailable is True
    with raises(ValidationError):
        ReportCardModel.model_validate(
            _card(
                top_memories=[
                    {
                        "id": MEM_B,
                        "type": "sight",
                        "summary": "旧摘要",
                        "unavailable": True,
                    }
                ]
            )
        )
    with raises(ValidationError):
        ReportSnapshot.model_validate(
            {
                "status": "ready",
                "report_id": REPORT_ID,
                "eligibility": _eligibility(eligible=True),
                "card": _card(status="ready", signature_line=None),
            }
        )


def test_stable_card_hash_ignores_line() -> None:
    first = ReportCardModel.model_validate(_card())
    second = ReportCardModel.model_validate(
        _card(status="ready", signature_line=STUB_SIGNATURE_LINE, version=4)
    )
    assert stable_card_hash(stable_card_fields(first.model_dump(mode="json"))) == stable_card_hash(
        stable_card_fields(second.model_dump(mode="json"))
    )


def test_openapi_has_report_routes() -> None:
    paths = export_openapi(create_app(Settings(app_env="test"))).document["paths"]
    assert "get" in paths["/api/v1/report"]
    assert "post" in paths["/api/v1/report/line"]
    assert UUID(REPORT_ID)


def test_enqueue_definer_is_api_only() -> None:
    root = Path(__file__).resolve().parents[2]
    sql = (
        root
        / "app"
        / "db"
        / "migrations"
        / "versions"
        / "20260908_0018_report_line_attempts.py"
    ).read_text(encoding="utf-8")
    assert "SET search_path = pg_catalog, public, private" in sql
    assert "GRANT EXECUTE ON FUNCTION private.enqueue_report_generate(uuid, uuid) TO kelin_api" in sql
    assert "enqueue_report_generate(uuid, uuid) TO kelin_worker" not in sql
    assert "enqueue_report_generate(uuid, uuid) TO kelin_scheduler" not in sql
    repo = getsource(__import__("app.repositories.report", fromlist=["insert_generate_outbox"]))
    assert "private.enqueue_report_generate(:report_id, :spirit_id)" in repo
