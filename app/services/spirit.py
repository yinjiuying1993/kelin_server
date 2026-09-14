"""Atomic idempotent spirit create. Spec §§5.4, 6.2, 6.3, 9.3.

User identity comes only from CurrentUser (JWT). Request must not carry user_id.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.invite_code import invite_code_is_valid
from app.domain.spirit import (
    SpiritCreateResult,
    create_spirit_request_hash,
    traits_for_egg,
)
from app.repositories import spirit as spirit_repo
from app.schemas.spirit import CreateSpiritRequest
from app.services.invite_code import InviteCodeAllocationError, allocate_invite_code


def _api_error(code: str, *, status_code: int, retryable: bool = False) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
    )


async def create_spirit_if_absent(
    session: AsyncSession,
    user: CurrentUser,
    request: CreateSpiritRequest,
) -> SpiritCreateResult:
    owner_id = user.id
    try:
        traits = traits_for_egg(request.egg)
    except KeyError as exc:
        raise _api_error("INVALID_INPUT", status_code=422) from exc
    request_hash = create_spirit_request_hash(request)

    await spirit_repo.lock_owner_create(session, owner_id)
    existing = await spirit_repo.fetch_spirit_for_owner(session, owner_id)
    if existing is not None:
        return await _replay_or_conflict(session, owner_id, request, request_hash, existing)

    claim = await spirit_repo.claim_create_idempotency(
        session, owner_id, request.client_id, request_hash
    )
    if not claim.inserted:
        return await _from_claimed_record(session, owner_id, request_hash, claim)

    await spirit_repo.insert_consents(
        session,
        owner_id,
        request.client_id,
        ai_version=request.consents.ai_disclosure.document_version,
        notice_version=request.consents.data_notice.document_version,
        terms_version=request.consents.user_terms.document_version,
    )

    async def reserve(code: str) -> None:
        await spirit_repo.insert_spirit(
            session,
            owner_id,
            request.client_id,
            name=request.name,
            egg=request.egg,
            invite_code=code,
            traits=traits,
        )

    try:
        await allocate_invite_code(session, reserve)
    except InviteCodeAllocationError as exc:
        raise _api_error("INTERNAL_ERROR", status_code=500) from exc
    except IntegrityError as exc:
        constraint = spirit_repo.integrity_constraint_name(exc)
        if constraint in {
            spirit_repo.SPIRIT_USER_UNIQUE,
            spirit_repo.SPIRIT_USER_CLIENT_UNIQUE,
        }:
            raced = await spirit_repo.fetch_spirit_for_owner(session, owner_id)
            if raced is not None:
                return await _replay_or_conflict(session, owner_id, request, request_hash, raced)
        raise

    await spirit_repo.insert_preferences(session, owner_id)
    result = await _require_result(session, owner_id)
    await spirit_repo.complete_create_idempotency(
        session, owner_id, request.client_id, result.spirit_id
    )
    return result


async def _replay_or_conflict(
    session: AsyncSession,
    owner_id: uuid.UUID,
    request: CreateSpiritRequest,
    request_hash: str,
    existing: spirit_repo.SpiritOwnerRow,
) -> SpiritCreateResult:
    if existing.client_id != request.client_id:
        raise _api_error("CONFLICT", status_code=409)
    record = await spirit_repo.fetch_create_idempotency(session, owner_id, request.client_id)
    if record is not None and record.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    return await _require_result(session, owner_id)


async def _from_claimed_record(
    session: AsyncSession,
    owner_id: uuid.UUID,
    request_hash: str,
    claim: spirit_repo.IdempotencyClaim,
) -> SpiritCreateResult:
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    return await _require_result(session, owner_id)


async def _require_result(session: AsyncSession, owner_id: uuid.UUID) -> SpiritCreateResult:
    result = await spirit_repo.fetch_create_result(session, owner_id)
    if result is None or not invite_code_is_valid(result.invite_code):
        raise _api_error("INTERNAL_ERROR", status_code=500)
    return result
