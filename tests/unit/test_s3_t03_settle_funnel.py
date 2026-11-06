"""S3-T03: all current-state entries call awareness settle_now; no second 18/72 machine."""

from __future__ import annotations

from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
SERVICES = APP / "services"

_BANNED = (
    "IDLE_STUDY_OR_AWAY",
    "IDLE_LOST",
    "desired_spirit_state",
    "timedelta(hours=18)",
    "timedelta(hours=72)",
)
_ALLOWED_HOURS = {
    APP / "domain" / "awareness" / "timeline.py",
}


def test_app_has_single_18_72_machine() -> None:
    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path in _ALLOWED_HOURS:
            assert "timedelta(hours=18)" in source
            assert "timedelta(hours=72)" in source
            continue
        for token in _BANNED:
            assert token not in source, f"{path}: {token}"


def test_entries_forward_to_settle_now() -> None:
    expected = {
        "bootstrap.py": "settle_then_resolve",
        "chat.py": "settle_now",
        "feed.py": "settle_now",
        "pact.py": "settle_now",
        "recall.py": "settle_now",
        "spirit_state.py": "settle_now",
        "state_scheduler.py": "settle_spirit_state",
        "visit_settle.py": "settle_visit_spirit_targets",
        "central/visit_target.py": "settle_then_resolve",
        "central/lifecycle.py": "settle_now",
    }
    for name, token in expected.items():
        source = (SERVICES / name).read_text(encoding="utf-8")
        assert token in source, name
        assert "desired_spirit_state" not in source
        assert "maybe_advance_stage" not in source or name in {
            "feed.py",
            "pact.py",
            "recall.py",
            "chat.py",
        }
