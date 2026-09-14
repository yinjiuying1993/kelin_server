from app.domain.sight_moderate import allowed_prop
from app.providers.sight_schema import require_safety_output
from app.providers.types import SafetyOutput, VisionOutput
from pydantic import ValidationError
from pytest import raises


def test_prop_whitelist_accepts_locked_kinds_only() -> None:
    assert allowed_prop("lamp") == "lamp"
    assert allowed_prop("plant") == "plant"
    assert allowed_prop("book") == "book"
    assert allowed_prop("object") == "object"
    assert allowed_prop("other") == "other"
    assert allowed_prop("dragon") is None
    assert allowed_prop("Lamp") is None


def test_safety_and_vision_outputs_forbid_extra_and_unknown_enums() -> None:
    assert require_safety_output({"decision": "allow"}) == SafetyOutput(decision="allow")
    assert require_safety_output({"decision": "block"}).decision == "block"
    with raises(Exception):
        require_safety_output({"decision": "allow", "reason": "secret"})
    with raises(Exception):
        require_safety_output({"decision": "maybe"})
    VisionOutput.model_validate({"summary": "窗台上的一盏灯", "prop": "lamp"})
    with raises(ValidationError):
        VisionOutput.model_validate({"summary": "灯", "prop": "lamp", "gps": "1,2"})
