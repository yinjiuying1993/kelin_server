from inspect import getsource
from pathlib import Path

from app.domain.growth import clamp_growth_value, compact_growth_payload
from app.repositories import growth as growth_repo
from app.services import growth as growth_service
from pytest import raises

_APP = Path(__file__).resolve().parents[2] / "app"


def test_clamp_growth_value_stays_in_0_100() -> None:
    assert clamp_growth_value(95, 10) == 100
    assert clamp_growth_value(5, -10) == 0
    assert clamp_growth_value(50, 0) == 50
    assert clamp_growth_value(100, 1) == 100
    assert clamp_growth_value(0, -1) == 0


def test_compact_growth_payload_drops_zero_and_rejects_unknown() -> None:
    assert compact_growth_payload({"hunger_delta": 10, "mood_delta": 0}) == {"hunger_delta": 10}
    assert compact_growth_payload({}) == {}
    with raises(ValueError, match="catalog"):
        compact_growth_payload({"stage": 1})


def test_apply_sql_clamps_and_growth_modules_have_no_network() -> None:
    sql = getsource(growth_repo.apply_growth_to_spirit)
    assert "GREATEST(0, LEAST(100" in sql
    assert "ON CONFLICT (source_type, source_id, event_type)" in getsource(
        growth_repo.insert_growth_event
    )
    assert "if not row.inserted" in getsource(growth_service.record_and_apply)
    for relative in ("repositories/growth.py", "services/growth.py"):
        text = (_APP / relative).read_text(encoding="utf-8")
        assert "httpx" not in text
        assert "bailian" not in text.lower()
        assert "BailianProvider" not in text
