from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import uuid4

from app.api.v1.chat_map import chat_turn_result_from_settlement
from app.api.v1.extract_map import extract_result_from_settlement
from app.api.v1.feed_map import feed_result_from_settlement
from app.api.v1.onboarding_map import onboarding_complete_result_from_settlement
from app.domain.chat import ChatTurnSettlement
from app.domain.extract import ExtractSettlement
from app.domain.onboarding import OnboardingCompleteSettlement
from app.domain.snapshot import patch_merge_decision, spirit_version_matches_patch
from app.domain.spirit import SpiritCreateResult
from app.repositories.feed import FeedRow
from app.schemas.bootstrap import RoomPublic
from app.schemas.spirit import QuotaUsage, SpiritPublic
from app.services.feed import FeedSettlement

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _spirit(
    *,
    version: int = 3,
    status: str = "home",
    stage: str = "formed",
    marks: tuple[str, ...] = (),
    hatched: datetime | None = NOW,
    step: int = 5,
) -> SpiritCreateResult:
    return SpiritCreateResult(
        spirit_id=uuid4(),
        client_id=uuid4(),
        name="雾生",
        egg="warm",
        invite_code="ABCD2345",
        closeness=65,
        curiosity=55,
        sharpness=35,
        nocturnal=40,
        stubborn=40,
        hunger=80,
        energy=80,
        mood=60,
        bond=20,
        stage=stage,
        status=status,
        scholar_marks=marks,
        version=version,
        onboarding_step=step,
        onboarding_completed_at=hatched,
        hatched_at=hatched,
        created_at=NOW,
        tts_on=False,
        push_on=False,
        visit_on=True,
        dnd_start=time(23, 0),
        dnd_end=time(8, 0),
        timezone="Asia/Shanghai",
        default_city=None,
        location_weather_on=False,
        remote_search_on=True,
    )


def test_onboarding_room_uses_status_layers_not_mark_keys() -> None:
    spirit = _spirit(marks=("interview-v1",), version=2)
    mapped = onboarding_complete_result_from_settlement(
        OnboardingCompleteSettlement(
            spirit=spirit,
            ordinary_dialogue_rounds=0,
            updated_at=NOW,
            replayed=False,
        )
    )
    assert mapped.patch.spirit is not None
    assert mapped.patch.spirit.status == "home"
    assert mapped.patch.spirit.stage == "formed"
    assert mapped.patch.spirit.scholar_marks == ["interview-v1"]
    assert mapped.patch.snapshot_version == spirit.version
    assert spirit_version_matches_patch(
        spirit_version=mapped.patch.spirit.version,
        snapshot_version=mapped.patch.snapshot_version,
    )
    assert mapped.patch.room is not None
    assert mapped.patch.room.layers == ["spirit", "scholar"]
    assert "interview-v1" not in mapped.patch.room.layers


def test_chat_patch_carries_status_stage_marks_and_server_quotas() -> None:
    spirit = _spirit(status="study", stage="awake", marks=("notes-v1",), version=8)
    quota = QuotaUsage(
        capability="chat",
        used=2,
        limit=100,
        reset_at="2026-09-09T16:00:00Z",
    )
    mapped = chat_turn_result_from_settlement(
        ChatTurnSettlement(
            spirit_id=spirit.spirit_id,
            user_message_id=uuid4(),
            spirit_message_id=uuid4(),
            conversation_window_id=uuid4(),
            onboarding=False,
            generation_source="stub",
            should_extract=False,
            onboarding_step=spirit.onboarding_step,
            ordinary_dialogue_rounds=3,
            version=spirit.version,
            replayed=False,
            last_interact_at=NOW,
            user_content="你好",
            spirit_content="嗯。",
            input_units=1,
            output_units=1,
            spirit=spirit,
            quotas=(quota,),
        )
    )
    assert mapped.patch.spirit is not None
    assert mapped.patch.spirit.status == "study"
    assert mapped.patch.spirit.stage == "awake"
    assert mapped.patch.spirit.scholar_marks == ["notes-v1"]
    assert mapped.patch.snapshot_version == 8
    assert mapped.patch.room is None
    assert mapped.quotas == [quota]
    assert patch_merge_decision(current_snapshot_version=8, patch_snapshot_version=8) == "merge"
    assert patch_merge_decision(current_snapshot_version=9, patch_snapshot_version=8) == "bootstrap"


def test_extract_patch_carries_authoritative_spirit_fields() -> None:
    spirit = _spirit(status="away", version=4)
    mapped = extract_result_from_settlement(
        ExtractSettlement(
            window_id=uuid4(),
            status="extracted",
            spirit=spirit,
            memories=(),
            style_samples=(),
            replayed=False,
        )
    )
    assert mapped.patch.spirit is not None
    assert mapped.patch.spirit.status == "away"
    assert mapped.patch.spirit.stage == "formed"
    assert mapped.patch.snapshot_version == mapped.patch.spirit.version
    assert mapped.patch.room is None
    assert mapped.quotas == []


def test_feed_patch_includes_spirit_room_and_quotas() -> None:
    spirit_id = uuid4()
    spirit = SpiritPublic.model_validate(
        {
            "id": str(spirit_id),
            "name": "雾生",
            "egg": "warm",
            "invite_code": "ABCD2345",
            "closeness": 65,
            "curiosity": 55,
            "sharpness": 35,
            "nocturnal": 40,
            "stubborn": 40,
            "hunger": 90,
            "energy": 80,
            "mood": 60,
            "bond": 20,
            "stage": "formed",
            "status": "lost",
            "scholar_marks": ["interview-v1"],
            "version": 11,
            "onboarding_step": 5,
            "onboarding_completed_at": "2026-09-09T12:00:00Z",
            "hatched_at": "2026-09-09T12:00:00Z",
            "created_at": "2026-09-08T00:00:00Z",
        }
    )
    room = RoomPublic.model_validate(
        {
            "weather": "cloudy",
            "layers": ["lost", "scholar"],
            "letter": {
                "type": "lost",
                "resource_id": str(spirit_id),
                "title_key": "room.letter.lost",
                "occurred_at": "2026-09-09T12:00:00Z",
            },
            "pending_sight": None,
            "unread_footprint_count": 0,
            "updated_at": "2026-09-09T12:00:00Z",
        }
    )
    quota = QuotaUsage(
        capability="food",
        used=1,
        limit=3,
        reset_at="2026-09-09T16:00:00Z",
    )
    mapped = feed_result_from_settlement(
        FeedSettlement(
            feed=FeedRow(
                id=uuid4(),
                kind="food",
                status="accepted",
                version=1,
                payload={},
                effect_applied_at=NOW,
                created_at=NOW,
                promise_status=None,
                remind_at=None,
                completed_at=None,
                rejection_code=None,
            ),
            snapshot_version=11,
            spirit=spirit,
            room=room,
            memories=(),
            quotas=(quota,),
            events=(),
        )
    )
    assert mapped.patch.spirit is not None
    assert mapped.patch.spirit.status == "lost"
    assert mapped.patch.spirit.stage == "formed"
    assert mapped.patch.spirit.scholar_marks == ["interview-v1"]
    assert mapped.patch.room is not None
    assert mapped.patch.room.layers == ["lost", "scholar"]
    assert mapped.patch.snapshot_version == 11
    assert mapped.quotas == [quota]
