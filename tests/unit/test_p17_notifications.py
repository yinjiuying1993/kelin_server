"""P17-T03 DND, daily cap, payload privacy, and care rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.domain.notifications import (
    DAILY_ACTIVE_NOTIFICATION_LIMIT,
    FIXED_ALERTS,
    care_is_eligible,
    fixed_alert_body,
    in_dnd,
    next_dnd_end,
    public_push_payload,
    push_dedupe_key,
    scheduled_for,
)

NOW = datetime(2026, 9, 12, 11, 0, tzinfo=UTC)
DND_NIGHT = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)
DND_MORNING = datetime(2026, 9, 12, 23, 30, tzinfo=UTC)
AFTER_DND = datetime(2026, 9, 13, 0, 30, tzinfo=UTC)


def test_payload_and_alert_are_public_only() -> None:
    resource = uuid4()
    payload = public_push_payload("postcard", resource)
    assert set(payload) == {"type", "resource_id"}
    assert payload["type"] == "postcard"
    assert payload["resource_id"] == str(resource)
    for kind, body in FIXED_ALERTS.items():
        assert set(public_push_payload(kind, resource)) == {"type", "resource_id"}
        alert = fixed_alert_body(kind)
        assert alert == body
        assert "记忆" not in alert
        assert "对话" not in alert
        assert "坐标" not in alert
        assert "Prompt" not in alert


def test_dnd_crosses_midnight_in_shanghai() -> None:
    assert in_dnd(NOW, "Asia/Shanghai") is False
    assert in_dnd(DND_NIGHT, "Asia/Shanghai") is True
    assert in_dnd(DND_MORNING, "Asia/Shanghai") is True
    assert in_dnd(AFTER_DND, "Asia/Shanghai") is False
    end = next_dnd_end(DND_NIGHT, "Asia/Shanghai")
    assert end == datetime(2026, 9, 13, 0, 0, tzinfo=UTC)
    delayed = scheduled_for(DND_NIGHT, "Asia/Shanghai")
    assert delayed == end


def test_daily_cap_and_care_conditions() -> None:
    assert DAILY_ACTIVE_NOTIFICATION_LIMIT == 2
    owner = uuid4()
    day = NOW.date()
    first = push_dedupe_key("postcard", uuid4(), owner, day)
    second = push_dedupe_key("pact", uuid4(), owner, day)
    assert first != second
    idle = NOW - timedelta(hours=18)
    assert care_is_eligible(
        push_on=True,
        status="home",
        last_interact_at=idle,
        now=NOW,
        in_quiet_hours=False,
        remaining_quota=2,
    )
    assert not care_is_eligible(
        push_on=True,
        status="lost",
        last_interact_at=idle,
        now=NOW,
        in_quiet_hours=False,
        remaining_quota=2,
    )
    assert not care_is_eligible(
        push_on=True,
        status="home",
        last_interact_at=idle,
        now=NOW,
        in_quiet_hours=True,
        remaining_quota=2,
    )
    assert not care_is_eligible(
        push_on=False,
        status="home",
        last_interact_at=idle,
        now=NOW,
        in_quiet_hours=False,
        remaining_quota=2,
    )
    assert not care_is_eligible(
        push_on=True,
        status="home",
        last_interact_at=idle,
        now=NOW,
        in_quiet_hours=False,
        remaining_quota=0,
    )
