from uuid import uuid4

import pytest
from app.domain.invite_code import normalize_invite_code
from app.schemas.social_api import (
    MAX_VISIT_DESTINATIONS,
    PUBLIC_NPC_FIELD_NAMES,
    PUBLIC_PROFILE_FIELD_NAMES,
    AddFriendRequest,
    NpcPublicProfile,
    PublicSpiritProfile,
    VisitPlanPublic,
)
from pydantic import ValidationError


def test_normalize_invite_code_trims_and_uppercases() -> None:
    assert normalize_invite_code(" abcd2345 ") == "ABCD2345"


def test_public_profile_whitelist_is_fixed() -> None:
    assert PublicSpiritProfile.model_fields.keys() == PUBLIC_PROFILE_FIELD_NAMES
    assert NpcPublicProfile.model_fields.keys() == PUBLIC_NPC_FIELD_NAMES
    assert MAX_VISIT_DESTINATIONS == 2


def test_add_friend_forbids_destination_selection() -> None:
    with pytest.raises(ValidationError):
        AddFriendRequest.model_validate(
            {
                "client_id": str(uuid4()),
                "invite_code": "ABCD2345",
                "host_spirit_id": str(uuid4()),
            }
        )
    with pytest.raises(ValidationError):
        AddFriendRequest.model_validate(
            {"client_id": str(uuid4()), "invite_code": "ABCD2345", "npc_id": "fog"}
        )


def test_visit_plan_rejects_duplicate_index() -> None:
    plan_id = str(uuid4())
    visit = {
        "type": "visit",
        "id": str(uuid4()),
        "plan_id": plan_id,
        "destination_index": 1,
        "status": "eligible",
        "host": {
            "id": str(uuid4()),
            "title": "阴天收集者",
            "stage": "formed",
            "public_marks": [],
            "status": "home",
        },
        "npc": None,
        "public_context": {
            "title": "阴天收集者",
            "stage": "formed",
            "weather": "cloudy",
            "public_marks": [],
        },
    }
    other = dict(visit)
    other["id"] = str(uuid4())
    with pytest.raises(ValidationError):
        VisitPlanPublic.model_validate({"plan_id": plan_id, "visits": [visit, other]})
