"""Map stored idempotency rows to replay / conflict / in-progress."""

from __future__ import annotations

from typing import Any

from app.core.errors import ApiError, public_error_message
from app.domain.central import classify_idempotency
from app.repositories.central.idempotency import CentralIdempotencyClaim


def _error(code: str, *, retryable: bool = False) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=409,
        retryable=retryable,
    )


def decide_claim(
    claim: CentralIdempotencyClaim, incoming_hash: str
) -> tuple[str, dict[str, Any] | None]:
    if claim.inserted:
        return "proceed", None
    outcome = classify_idempotency(
        stored_hash=claim.request_hash,
        stored_state=claim.status,
        incoming_hash=incoming_hash,
    )
    if outcome == "replay":
        if claim.response_json is None:
            raise _error("IDEMPOTENCY_IN_PROGRESS", retryable=True)
        return "replay", claim.response_json
    if outcome == "conflict":
        raise _error("IDEMPOTENCY_CONFLICT")
    raise _error("IDEMPOTENCY_IN_PROGRESS", retryable=True)
