"""Submit one piece of feedback. Safety runs outside the write transaction."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.feedback import (
    FEEDBACK_REJECTION_CODE,
    FEEDBACK_RETRY_AFTER_MS,
)
from app.providers.errors import ProviderError
from app.repositories import feedback as feedback_repo
from app.schemas.feedback import FeedbackRequest


class TextSafety(Protocol):
    async def decision(self, text: str) -> str: ...


def _error(
    code: str,
    status_code: int,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
        details=details,
    )


def _busy() -> ApiError:
    return _error(
        "IDEMPOTENCY_IN_PROGRESS",
        409,
        retryable=True,
        details={"retry_after_ms": FEEDBACK_RETRY_AFTER_MS},
    )


def _conflict() -> ApiError:
    return _error("IDEMPOTENCY_CONFLICT", 409)


def _rejected() -> ApiError:
    return _error(FEEDBACK_REJECTION_CODE, 422)


def _provider_failure(exc: ProviderError) -> ApiError:
    if exc.code == "PROVIDER_TIMEOUT":
        return _error("PROVIDER_TIMEOUT", 504, retryable=True)
    if exc.code == "RATE_LIMITED":
        return _error("RATE_LIMITED", 429, retryable=True)
    return _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True)


def _stored_rejection(claim: feedback_repo.FeedbackClaim) -> bool:
    payload = claim.response_json or {}
    return payload.get("code") == FEEDBACK_REJECTION_CODE and claim.resource_id is None


def _finished(claim: feedback_repo.FeedbackClaim, body: str) -> uuid.UUID | None:
    if claim.request_hash != body:
        raise _conflict()
    if claim.status == "completed" and claim.resource_id is not None:
        return claim.resource_id
    if claim.status == "completed" and _stored_rejection(claim):
        raise _rejected()
    if claim.status == "conflict":
        raise _conflict()
    return None


def _replay_or_raise(claim: feedback_repo.FeedbackClaim, body: str) -> uuid.UUID | None:
    finished = _finished(claim, body)
    if finished is not None:
        return finished
    if claim.inserted:
        return None
    raise _busy()


async def submit_feedback(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    request: FeedbackRequest,
    safety: TextSafety,
) -> uuid.UUID:
    body = request.text
    async with claimed_transaction(factory, user) as session:
        claim = await feedback_repo.claim_feedback(session, user.id, request.client_id, body)
        replayed = _replay_or_raise(claim, body)
    if replayed is not None:
        return replayed
    try:
        decision = await safety.decision(body)
    except ProviderError as exc:
        await _release(factory, user, request.client_id)
        raise _provider_failure(exc) from exc
    if decision == "block":
        await _store_rejection(factory, user, request.client_id)
        raise _rejected()
    if decision != "allow":
        await _release(factory, user, request.client_id)
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True)
    async with claimed_transaction(factory, user) as session:
        return await _commit(session, user.id, request.client_id, body)


async def _commit(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    body: str,
) -> uuid.UUID:
    claim = await feedback_repo.lock_feedback_claim(session, owner_id, client_id)
    if claim is None:
        raise _busy()
    finished = _finished(claim, body)
    if finished is not None:
        return finished
    if claim.status != "in_progress":
        raise _busy()
    feedback_id = await feedback_repo.insert_feedback(session, owner_id, client_id, body)
    await feedback_repo.complete_feedback(session, owner_id, client_id, feedback_id)
    return feedback_id


async def _store_rejection(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    client_id: uuid.UUID,
) -> None:
    async with claimed_transaction(factory, user) as session:
        await feedback_repo.reject_feedback(
            session,
            user.id,
            client_id,
            FEEDBACK_REJECTION_CODE,
        )


async def _release(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    client_id: uuid.UUID,
) -> None:
    async with claimed_transaction(factory, user) as session:
        await feedback_repo.release_feedback_claim(session, user.id, client_id)
