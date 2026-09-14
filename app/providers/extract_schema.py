"""ExtractOutput whitelist. Spec §§8.3, 16.2."""

from __future__ import annotations

from pydantic import ValidationError

from app.providers.errors import ProviderError
from app.providers.types import ExtractOutput


def require_extract_output(value: object) -> ExtractOutput:
    try:
        if isinstance(value, ExtractOutput):
            return ExtractOutput.model_validate(value.model_dump())
        return ExtractOutput.model_validate(value)
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
