"""Idempotent onboarding complete. Spec §§8.2, 9.4.

hatched_at is written only when onboarding_step=5 and five complete pairs exist.
Replay with the same client_id returns that first hatched_at. Does not infer
completion from step alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.onboarding import (
    OnboardingCompleteSettlement,
    complete_onboarding_request_hash,
    onboarding_ready_to_hatch,
)
from app.domain.spirit_state import require_aware
from app.repositories import chat as chat_repo
from app.repositories import onboarding as onboarding_repo
from app.repositories import spirit as spirit_repo
from app.schemas.onboarding import CompleteOnboardingRequest


def _api_error(code: str, *, status_code: int, retryable: bool = False) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
    )


async def complete_onboarding(
    session: AsyncSession,
    user: CurrentUser,
    request: CompleteOnboardingRequest,
    *,
    now: datetime,
) -> OnboardingCompleteSettlement:
    require_aware(now, field="now")
    await onboarding_repo.assert_writable_transaction(session)
    locked = await chat_repo.lock_spirit_for_owner(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = complete_onboarding_request_hash(request)

    if locked.hatched_at is not None:
        return await _replay_or_already_completed(session, user.id, request.client_id, request_hash)

    if request.expected_spirit_version != locked.version:
        raise _api_error("CONFLICT", status_code=409)

    complete_rounds = await onboarding_repo.count_complete_onboarding_rounds(session, locked.id)
    if not onboarding_ready_to_hatch(step=locked.onboarding_step, complete_rounds=complete_rounds):
        raise _api_error("ONBOARDING_INCOMPLETE", status_code=409)

    claim = await onboarding_repo.claim_complete_idempotency(
        session, user.id, request.client_id, request_hash
    )
    if not claim.inserted:
        return await _from_claimed_record(session, user.id, request_hash, claim)

    hatched = await onboarding_repo.apply_hatch(session, user.id, locked.id, now=now)
    if hatched is None:
        raise _api_error("ONBOARDING_INCOMPLETE", status_code=409)
    await onboarding_repo.complete_complete_idempotency(
        session, user.id, request.client_id, locked.id
    )
    return await _require_settlement(session, user.id, replayed=False)


async def _replay_or_already_completed(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> OnboardingCompleteSettlement:
    existing = await onboarding_repo.fetch_complete_idempotency(session, owner_id, client_id)
    if existing is None:
        raise _api_error("ONBOARDING_ALREADY_COMPLETED", status_code=409)
    return await _from_claimed_record(session, owner_id, request_hash, existing)


async def _from_claimed_record(
    session: AsyncSession,
    owner_id: uuid.UUID,
    request_hash: str,
    claim: onboarding_repo.CompleteIdempotencyClaim,
) -> OnboardingCompleteSettlement:
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    return await _require_settlement(session, owner_id, replayed=True)


async def _require_settlement(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    replayed: bool,
) -> OnboardingCompleteSettlement:
    result = await spirit_repo.fetch_create_result(session, owner_id)
    projection = await onboarding_repo.fetch_hatch_projection(session, owner_id)
    if (
        result is None
        or projection is None
        or result.hatched_at is None
        or result.onboarding_completed_at is None
        or result.onboarding_step != 5
    ):
        raise _api_error("INTERNAL_ERROR", status_code=500)
    return OnboardingCompleteSettlement(
        spirit=result,
        ordinary_dialogue_rounds=projection.ordinary_dialogue_rounds,
        updated_at=projection.updated_at,
        replayed=replayed,
    )
