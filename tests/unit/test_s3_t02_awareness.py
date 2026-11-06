"""S3-T02 awareness persistence does not write mood, label, or bond."""

from __future__ import annotations

import inspect
from pathlib import Path

from app.services.awareness.settle_service import settle_now

REPO = (
    Path(__file__).resolve().parents[2] / "app" / "repositories" / "awareness.py"
).read_text(encoding="utf-8")
SERVICE = (
    Path(__file__).resolve().parents[2] / "app" / "services" / "awareness" / "settle_service.py"
).read_text(encoding="utf-8")


def test_settle_now_does_not_accept_client_status() -> None:
    params = inspect.signature(settle_now).parameters
    assert "now" in params
    assert params["now"].kind is inspect.Parameter.KEYWORD_ONLY
    assert "status" not in params
    assert "mood" not in params
    assert "bond" not in params
    assert "primary_label" not in params


def test_apply_sql_only_updates_awareness_life_and_status() -> None:
    assert "UPDATE public.spirit_awareness" in REPO
    assert "last_state_settled_at" in REPO
    assert "mood" not in REPO
    assert "primary_label" not in REPO
    assert "bond" not in REPO
    assert "UPDATE public.spirits" not in REPO
    assert "mood =" not in REPO
    assert "spirit_emotion" not in REPO
    assert "spirit_growth" not in REPO
    assert "FOR UPDATE OF a" in REPO


def test_service_does_not_write_emotion_or_growth() -> None:
    assert "settle(" in SERVICE
    assert "mood =" not in SERVICE
    assert "primary_label" not in SERVICE
    assert "bond" not in SERVICE
    assert "app.services.growth" not in SERVICE
    assert "app.services.emotion" not in SERVICE
    assert "app.repositories.growth" not in SERVICE
    assert "app.repositories.emotion" not in SERVICE
