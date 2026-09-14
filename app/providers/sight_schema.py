"""Safety/Vision output whitelist. Spec §§11.4, 16.2."""

from __future__ import annotations

from pydantic import ValidationError

from app.providers.errors import ProviderError
from app.providers.types import SafetyOutput, VisionOutput


def require_safety_output(value: object) -> SafetyOutput:
    try:
        if isinstance(value, SafetyOutput):
            return SafetyOutput.model_validate(value.model_dump())
        return SafetyOutput.model_validate(value)
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc


def require_vision_output(value: object) -> VisionOutput:
    try:
        if isinstance(value, VisionOutput):
            return VisionOutput.model_validate(value.model_dump())
        return VisionOutput.model_validate(value)
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
