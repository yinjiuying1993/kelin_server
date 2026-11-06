from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import uuid4

from app.api.v1.spirit_map import mutation_result_from_create
from app.domain.invite_code import invite_code_is_valid
from app.domain.spirit import SpiritCreateResult
from app.schemas.spirit import MutationResult


def test_create_result_maps_to_hatched_room_patch() -> None:
    spirit_id = uuid4()
    created = datetime(2026, 9, 8, tzinfo=UTC)
    hatched = datetime(2026, 9, 8, 0, 0, 1, tzinfo=UTC)
    result = SpiritCreateResult(
        spirit_id=spirit_id,
        client_id=uuid4(),
        name="未名",
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
        bond=0,
        stage="whelp",
        status="home",
        scholar_marks=(),
        version=1,
        onboarding_step=0,
        onboarding_completed_at=None,
        hatched_at=hatched,
        created_at=created,
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
    mapped = mutation_result_from_create(result)
    parsed = MutationResult.model_validate(mapped.model_dump(mode="json"))
    assert parsed.resource.id == spirit_id
    assert parsed.resource.version == 1
    assert parsed.patch.snapshot_version == 1
    assert parsed.patch.spirit is not None
    assert parsed.patch.spirit.egg == "warm"
    assert parsed.patch.spirit.status == "home"
    assert parsed.patch.spirit.stage == "formed"
    assert parsed.patch.spirit.hatched_at == "2026-09-08T00:00:01Z"
    assert parsed.patch.spirit.version == parsed.patch.snapshot_version
    assert parsed.patch.spirit.model_dump().get("scholar_marks") is None
    assert parsed.patch.spirit.model_dump().get("onboarding_step") is None
    assert parsed.patch.room is not None
    assert parsed.patch.room.stage_image_key == "formed_image"
    assert parsed.patch.room.letter_key is None
    assert parsed.patch.room.text_status == "home"
    assert parsed.patch.room.status == "home"
    assert parsed.quotas == []
    assert invite_code_is_valid(parsed.patch.spirit.invite_code)
    assert parsed.patch.preferences is None
    assert parsed.patch.model_dump().get("onboarding") is None
    assert parsed.patch.spirit.created_at == "2026-09-08T00:00:00Z"
