"""Dev-only Debug HTTP surface. Spec §14.10."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.deps import session_factory_from_app
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.logging import get_logger, hash_user_id
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_read_transaction, claimed_transaction
from app.domain.debug import debug_identity_allowed, source_host_allowed
from app.schemas.debug import (
    DebugErrorEnvelope,
    DebugFailureRequest,
    DebugMemoryCreateRequest,
    DebugMutationPatch,
    DebugMutationResult,
    DebugReportEligibilityRequest,
    DebugResetRequest,
    DebugSpiritStateRequest,
    DebugSpiritTimeRequest,
    DebugSuccessEnvelope,
    DebugUsageSnapshot,
)
from app.services.debug import (
    DebugSettlement,
    apply_report_eligibility,
    apply_spirit_state,
    apply_spirit_time,
    create_memory,
    inject_failure,
    load_usage,
    reset_account,
)

router = APIRouter(prefix="/api/v1/debug", tags=["debug"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": DebugErrorEnvelope, "description": "UNAUTHENTICATED"},
    403: {"model": DebugErrorEnvelope, "description": "FORBIDDEN"},
    404: {"model": DebugErrorEnvelope, "description": "NOT_FOUND"},
    422: {"model": DebugErrorEnvelope, "description": "INVALID_INPUT"},
}


def _forbidden() -> ApiError:
    return ApiError(
        "FORBIDDEN",
        public_error_message("FORBIDDEN"),
        status_code=403,
        retryable=False,
    )


async def require_debug_user(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> CurrentUser:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _forbidden()
    host = request.client.host if request.client is not None else None
    if not source_host_allowed(host, app_env=settings.app_env):
        get_logger(capability="debug").info(
            "debug_forbidden_source",
            user_id_hash=hash_user_id(user.id),
        )
        raise _forbidden()
    provided = request.headers.get("x-debug-token")
    if not debug_identity_allowed(user_id=user.id, settings=settings, provided_token=provided):
        get_logger(capability="debug").info(
            "debug_forbidden_identity",
            user_id_hash=hash_user_id(user.id),
        )
        raise _forbidden()
    return user


def _mutation_payload(settlement: DebugSettlement) -> DebugMutationResult:
    return DebugMutationResult(
        resource={
            "type": "debug",
            "id": str(settlement.spirit_id),
            "version": settlement.snapshot_version,
        },
        patch=DebugMutationPatch(
            snapshot_version=settlement.snapshot_version,
            spirit=settlement.spirit,
            memories_upsert=list(settlement.memories),
        ),
        quotas=list(settlement.quotas),
    )


@router.post(
    "/spirit/state",
    response_model=DebugSuccessEnvelope,
    responses=_ERRORS,
)
async def post_spirit_state(
    request: Request,
    body: DebugSpiritStateRequest,
    user: Annotated[CurrentUser, Depends(require_debug_user)],
) -> DebugSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    async with claimed_transaction(factory, user) as session:
        settled = await apply_spirit_state(session, user, body, now=now)
    return DebugSuccessEnvelope(
        data=_mutation_payload(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/spirit/time",
    response_model=DebugSuccessEnvelope,
    responses=_ERRORS,
)
async def post_spirit_time(
    request: Request,
    body: DebugSpiritTimeRequest,
    user: Annotated[CurrentUser, Depends(require_debug_user)],
) -> DebugSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    async with claimed_transaction(factory, user) as session:
        settled = await apply_spirit_time(session, user, body, now=now)
    return DebugSuccessEnvelope(
        data=_mutation_payload(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/memories",
    response_model=DebugSuccessEnvelope,
    responses=_ERRORS,
)
async def post_memories(
    request: Request,
    body: DebugMemoryCreateRequest,
    user: Annotated[CurrentUser, Depends(require_debug_user)],
) -> DebugSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    async with claimed_transaction(factory, user) as session:
        settled = await create_memory(session, user, body, now=now)
    return DebugSuccessEnvelope(
        data=_mutation_payload(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/report/eligibility",
    response_model=DebugSuccessEnvelope,
    responses=_ERRORS,
)
async def post_report_eligibility(
    request: Request,
    body: DebugReportEligibilityRequest,
    user: Annotated[CurrentUser, Depends(require_debug_user)],
) -> DebugSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    async with claimed_transaction(factory, user) as session:
        settled = await apply_report_eligibility(session, user, body, now=now)
    return DebugSuccessEnvelope(
        data=_mutation_payload(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/failures",
    response_model=DebugSuccessEnvelope,
    responses=_ERRORS,
)
async def post_failures(
    request: Request,
    body: DebugFailureRequest,
    user: Annotated[CurrentUser, Depends(require_debug_user)],
) -> DebugSuccessEnvelope:
    injected = inject_failure(user, body)
    return DebugSuccessEnvelope(
        data=DebugMutationResult(
            resource={
                "type": "debug_failure",
                "capability": injected.capability,
                "mode": injected.mode,
                "remaining_calls": injected.remaining_calls,
            },
            patch=DebugMutationPatch(snapshot_version=1),
        ),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/usage",
    response_model=DebugSuccessEnvelope,
    responses=_ERRORS,
)
async def get_usage(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_debug_user)],
) -> DebugSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    async with claimed_read_transaction(factory, user) as session:
        usage = await load_usage(session, user, now=now)
    return DebugSuccessEnvelope(
        data=DebugUsageSnapshot(
            snapshot_version=usage.snapshot_version,
            quotas=list(usage.quotas),
        ),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.delete(
    "/reset",
    response_model=DebugSuccessEnvelope,
    responses=_ERRORS,
)
async def delete_reset(
    request: Request,
    body: DebugResetRequest,
    user: Annotated[CurrentUser, Depends(require_debug_user)],
) -> DebugSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    async with claimed_transaction(factory, user) as session:
        settled = await reset_account(session, user, body, now=now)
    return DebugSuccessEnvelope(
        data=_mutation_payload(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
