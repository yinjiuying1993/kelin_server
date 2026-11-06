"""S4-T03: room projection assembles the four-piece and does not recompute sources."""

from __future__ import annotations

import ast
from pathlib import Path

from app.domain.central.views import AwarenessView, EmotionView, GrowthView
from app.domain.room import ProjectionInput, RoomProjectionPatch, project
from app.services.room import assemble

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "app" / "domain" / "room"
APP_SERVICES = ROOT / "app" / "services"


def _input(**overrides: object) -> ProjectionInput:
    values: dict[str, object] = {
        "stage": "whelp",
        "primary_emotion_label": "calm",
        "status": "home",
        "hunger_band": "L2",
        "energy_band": "L2",
        "stage_image_key": "whelp_image",
    }
    values.update(overrides)
    return ProjectionInput(**values)  # type: ignore[arg-type]


def test_patch_has_four_piece_and_status() -> None:
    patch = project(_input())
    assert patch.stage_image_key == "whelp_image"
    assert patch.primary_emotion_label == "平静"
    assert patch.letter_key is None
    assert patch.text_status == "home"
    assert patch.status == "home"
    assert not hasattr(patch, "weather")
    assert not hasattr(patch, "motion")
    assert not hasattr(patch, "light")
    assert not hasattr(patch, "layers")
    assert not hasattr(patch, "notification_actions")


def test_realtime_emotion_does_not_change_stage_image() -> None:
    calm = project(_input(primary_emotion_label="calm", stage_image_key="formed_image"))
    hurt = project(
        _input(
            primary_emotion_label="hurt",
            stage_image_key="formed_image",
            hunger_band="L0",
            energy_band="L0",
        )
    )
    cleared = project(
        _input(primary_emotion_label="uneasy", stage="formed", stage_image_key="formed_image")
    )
    assert calm.stage_image_key == hurt.stage_image_key == cleared.stage_image_key == "formed_image"
    assert hurt.primary_emotion_label == "委屈"
    assert hurt.status == "home"


def test_lost_letter_beats_due_promise_and_care() -> None:
    patch = project(
        _input(
            status="lost",
            letter_candidates=("care_in_app", "promise_due", "pact_ready", "lost_recall"),
        )
    )
    assert patch.letter_key == "promise_due"
    assert patch.status == "home"
    home_due = project(
        _input(status="home", letter_candidates=("care_in_app", "promise_due", "pact_ready"))
    )
    assert home_due.letter_key == "promise_due"
    recap = project(_input(status="home", letter_candidates=("care_in_app", "pact_ready")))
    assert recap.letter_key == "pact_ready"
    care = project(_input(status="home", letter_candidates=("care_in_app",)))
    assert care.letter_key == "care_in_app"


def test_status_default_letters_and_aliases() -> None:
    assert project(_input(status="study")).letter_key == "away_visit"
    assert project(_input(status="study")).status == "away"
    assert project(_input(status="away")).letter_key == "away_visit"
    lost_alias = project(_input(status="lost", letter_candidates=("lost", "care")))
    assert lost_alias.status == "home"
    assert lost_alias.letter_key == "care_in_app"
    away_due = project(_input(status="away", letter_candidates=("away", "promise_due")))
    assert away_due.letter_key == "promise_due"


def test_text_status_keeps_destination_when_offline() -> None:
    patch = project(_input(status="away", connectivity="offline"))
    assert patch.status == "away"
    assert patch.text_status == "away_offline"
    hungry = project(_input(status="home", hunger_band="L0", energy_band="L3"))
    assert hungry.text_status == "home"
    assert hungry.status == "home"
    both = project(_input(status="home", hunger_band="L0", energy_band="L0"))
    assert both.text_status == "home"


def test_growth_stage_stub_maps_image_without_emotion() -> None:
    patch = project(_input(stage="awake", stage_image_key=None, primary_emotion_label="curious"))
    assert patch.stage_image_key == "awake_image"
    assert patch.primary_emotion_label == "好奇"


def test_assemble_ignores_realtime_emotion_and_uses_views() -> None:
    room = assemble(
        awareness=AwarenessView(
            status="lost",
            hunger_band="L0",
            energy_band="L0",
            letter_candidates=("lost", "promise_due"),
        ),
        emotion=EmotionView(
            primary_label="uneasy",
            realtime_emotion="happy",
            mood_band="M0",
            reply_budget=1,
        ),
        growth=GrowthView(
            stage="whelp",
            stage_image_key="whelp_image",
            trait_bands=("T2",),
            cognition_band="C1",
        ),
    )
    assert room.stage_image_key == "whelp_image"
    assert room.primary_emotion_label == "不安"
    assert room.letter_key == "promise_due"
    assert room.status == "home"
    assert room.text_status == "home"


def test_domain_has_no_io_or_source_settlement() -> None:
    banned = (
        "sqlalchemy",
        "AsyncSession",
        "spirit_awareness",
        "spirit_emotion",
        "spirit_growth",
        "idle_hours",
        "LABEL_RANK",
        "decay_life",
        "timedelta(hours=18)",
        "timedelta(hours=72)",
    )
    skip = {"legacy.py"}
    for path in DOMAIN.rglob("*.py"):
        if path.name in skip:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("sqlalchemy") for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("sqlalchemy")
                assert not node.module.startswith("app.services")
                assert not node.module.startswith("app.repositories")
                assert not node.module.startswith("app.domain.awareness")
                assert not node.module.startswith("app.domain.emotion")
                assert not node.module.startswith("app.domain.growth")
        source = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in source, f"{path}: {token}"


def test_other_modules_do_not_assemble_four_piece() -> None:
    tokens = ("RoomProjectionPatch", "pick_letter_key", "ProjectionInput")
    owned = {
        APP_SERVICES / "room" / "projection_service.py",
        APP_SERVICES / "room" / "__init__.py",
    }
    for path in APP_SERVICES.rglob("*.py"):
        if path in owned:
            continue
        source = path.read_text(encoding="utf-8")
        for token in tokens:
            assert token not in source, f"{path}: {token}"
    patch_fields = set(RoomProjectionPatch.__dataclass_fields__)
    assert patch_fields == {
        "stage_image_key",
        "primary_emotion_label",
        "letter_key",
        "text_status",
        "status",
        "snapshot_version",
    }
