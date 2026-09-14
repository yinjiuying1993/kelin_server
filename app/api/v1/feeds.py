"""Feed and promise HTTP surface. Spec §§8.4, 8.4.1, 11.1, 11.2."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app
from app.api.v1.feed_map import feed_result_from_settlement
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.feed import (
    FeedCreateRequest,
    FeedErrorEnvelope,
    FeedSuccessEnvelope,
    PromiseActionRequest,
    PromisePatchRequest,
)
from app.services.feed import cancel_promise, complete_promise, patch_promise, settle_feed

router = APIRouter(prefix="/api/v1", tags=["feed"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": FeedErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": FeedErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": FeedErrorEnvelope,
        "description": "CONFLICT, IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {"model": FeedErrorEnvelope, "description": "INVALID_INPUT"},
    429: {"model": FeedErrorEnvelope, "description": "QUOTA_EXCEEDED"},
    503: {"model": FeedErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}

_MUTATION_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": FeedErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": FeedErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": FeedErrorEnvelope,
        "description": (
            "CONFLICT, PROMISE_NOT_ACTIVE, IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS"
        ),
    },
    422: {"model": FeedErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": FeedErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.post(
    "/feed",
    response_model=FeedSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def post_feed(
    request: Request,
    body: FeedCreateRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> FeedSuccessEnvelope:
    """Create a feed. Spec §§8.4, 11.1."""
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, _user) as session:
            settled = await settle_feed(session, _user, body, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return FeedSuccessEnvelope(
        data=feed_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.patch(
    "/feeds/{feed_id}",
    response_model=FeedSuccessEnvelope,
    responses=_MUTATION_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def patch_feed(
    request: Request,
    feed_id: UUID,
    body: PromisePatchRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> FeedSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, _user) as session:
            settled = await patch_promise(session, _user, feed_id, body, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return FeedSuccessEnvelope(
        data=feed_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/feeds/{feed_id}/complete",
    response_model=FeedSuccessEnvelope,
    responses=_MUTATION_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def post_feed_complete(
    request: Request,
    feed_id: UUID,
    body: PromiseActionRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> FeedSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, _user) as session:
            settled = await complete_promise(session, _user, feed_id, body, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return FeedSuccessEnvelope(
        data=feed_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/feeds/{feed_id}/cancel",
    response_model=FeedSuccessEnvelope,
    responses=_MUTATION_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def post_feed_cancel(
    request: Request,
    feed_id: UUID,
    body: PromiseActionRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> FeedSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, _user) as session:
            settled = await cancel_promise(session, _user, feed_id, body, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return FeedSuccessEnvelope(
        data=feed_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
