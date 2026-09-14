"""ChatOutput whitelist and citation owner checks. Spec §16.2."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import ValidationError

from app.providers.errors import ProviderError
from app.providers.types import ChatCitation, ChatOutput


def require_chat_output(value: object) -> ChatOutput:
    try:
        if isinstance(value, ChatOutput):
            return ChatOutput.model_validate(value.model_dump())
        return ChatOutput.model_validate(value)
    except ValidationError as exc:
        raise ProviderError("MODEL_UNAVAILABLE") from exc


def verified_memory_ids(
    citations: Sequence[ChatCitation],
    *,
    allowed_ids: frozenset[UUID],
) -> tuple[UUID, ...]:
    verified: list[UUID] = []
    seen: set[UUID] = set()
    for citation in citations:
        if citation.type != "memory" or citation.id not in allowed_ids:
            raise ProviderError("MODEL_UNAVAILABLE")
        if citation.id in seen:
            continue
        seen.add(citation.id)
        verified.append(citation.id)
    return tuple(verified)
