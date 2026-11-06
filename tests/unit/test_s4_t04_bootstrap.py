"""S4-T04: public room is the four-piece; empty letter stays null; no layers."""

from __future__ import annotations

from pathlib import Path

from app.api.v1.public_map import room_public_assembled, room_public_v1
from app.schemas.room import RoomPublic

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "app" / "services" / "bootstrap.py"
PUBLIC_MAP = ROOT / "app" / "api" / "v1" / "public_map.py"
ONBOARDING_MAP = ROOT / "app" / "api" / "v1" / "onboarding_map.py"
SETTINGS_MAP = ROOT / "app" / "api" / "v1" / "settings_map.py"


def test_assembled_room_matches_s1_four_piece() -> None:
    room = room_public_assembled(
        status="home",
        stage="whelp",
        hunger=80,
        energy=80,
        primary_emotion_label="calm",
    )
    dumped = room.model_dump()
    assert dumped == {
        "stage_image_key": "formed_image",
        "letter_key": None,
        "text_status": "home",
        "status": "home",
    }
    assert "weather" not in dumped
    assert "layers" not in dumped
    assert "pending_sight" not in dumped
    RoomPublic.model_validate(dumped)


def test_empty_letter_is_null_not_fabricated_care() -> None:
    room = room_public_assembled(
        status="home",
        stage="whelp",
        hunger=80,
        energy=80,
        primary_emotion_label=None,
        letter_candidates=(),
    )
    assert room.letter_key is None
    explicit = room_public_v1(
        stage="formed",
        status="study",
        letter_key=None,
        primary_emotion_label=None,
    )
    assert explicit.letter_key is None
    assert explicit.stage_image_key == "formed_image"


def test_lost_letter_beats_due_promise_in_public_room() -> None:
    room = room_public_assembled(
        status="lost",
        stage="whelp",
        hunger=10,
        energy=10,
        primary_emotion_label="uneasy",
        letter_candidates=("promise_due", "care_in_app", "lost_recall"),
    )
    assert room.letter_key == "promise_due"
    assert room.status == "home"
    assert room.stage_image_key == "formed_image"
    dumped = room.model_dump()
    assert dumped.get("layers") is None
    assert dumped.get("weather") is None


def test_bootstrap_and_maps_do_not_return_layers() -> None:
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    public_map = PUBLIC_MAP.read_text(encoding="utf-8")
    onboarding = ONBOARDING_MAP.read_text(encoding="utf-8")
    settings = SETTINGS_MAP.read_text(encoding="utf-8")
    assert "room_public_assembled" in bootstrap
    for source in (bootstrap, public_map, onboarding, settings):
        assert "project_room(" not in source
        assert "pending_sight" not in source
        assert ".layers" not in source
    assert "weather" not in public_map
    assert "weather" not in onboarding

