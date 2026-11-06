"""ExtractOutput whitelist. Spec §§8.3, 16.2."""

from __future__ import annotations

from pydantic import ValidationError

from app.domain.extract import discard_extract_side_channels
from app.providers.errors import ProviderError
from app.providers.types import ExtractOutput


def require_extract_output(value: object) -> ExtractOutput:
    try:
        if isinstance(value, ExtractOutput):
            payload: object = value.model_dump()
        else:
            payload = value
        return ExtractOutput.model_validate(discard_extract_side_channels(payload))
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc
