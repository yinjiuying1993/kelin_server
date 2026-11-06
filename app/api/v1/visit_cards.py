"""Visit message card HTTP surface."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.social_api import SocialErrorEnvelope
from app.schemas.visit_card import (
    VisitCardDraftEnvelope,
    VisitCardReadEnvelope,
    VisitCardReplyEnvelope,
    VisitCardReplyRequest,
    VisitCardSaveRequest,
    VisitCardSealedEnvelope,
    VisitCardUploadEnvelope,
    VisitCardUploadRequest,
    VisitFootprintPageEnvelope,
)
from app.services.visit_cards import (
    issue_visit_card_upload,
    list_visit_footprints,
    mark_visit_card_read,
    read_owned_visit_card,
    read_sealed_visit_card,
    reply_owned_visit_card,
    save_owned_visit_card,
)

router = APIRouter(prefix="/api/v1", tags=["visit-cards"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SocialErrorEnvelope, "description": "UNAUTHENTICATED"},
    403: {"model": SocialErrorEnvelope, "description": "NOT_VISIT_HOST"},
    404: {"model": SocialErrorEnvelope, "description": "NOT_FOUND"},
    409: {"model": SocialErrorEnvelope, "description": "IDEMPOTENCY_CONFLICT or VISIT_CARD_CLOSED"},
    422: {"model": SocialErrorEnvelope, "description": "CARD_EMPTY or rejected card content"},
    503: {"model": SocialErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


def _signing_key(request: Request) -> bytes:
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings.cursor_signing_key()
    return b"kelin-dev-cursor-hmac"


@router.get(
    "/visit-card",
    response_model=VisitCardDraftEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Read the card prepared for the next visit",
)
async def get_visit_card(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> VisitCardDraftEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            draft = await read_owned_visit_card(session)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return VisitCardDraftEnvelope(
        data=draft,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.put(
    "/visit-card",
    response_model=VisitCardDraftEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Save the card prepared for the next visit",
)
async def put_visit_card(
    request: Request,
    body: VisitCardSaveRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> VisitCardDraftEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            draft = await save_owned_visit_card(session, body)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return VisitCardDraftEnvelope(
        data=draft,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/visit-cards/{card_id}",
    response_model=VisitCardSealedEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Read a sealed visit card",
)
async def get_sealed_visit_card(
    request: Request,
    card_id: UUID,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> VisitCardSealedEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            card = await read_sealed_visit_card(
                session,
                card_id=card_id,
                secret=_signing_key(request),
                now=datetime.now(UTC),
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return VisitCardSealedEnvelope(
        data=card,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/visit-cards/{card_id}/reply",
    response_model=VisitCardReplyEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Reply to a sealed visit card",
)
async def post_visit_card_reply(
    request: Request,
    card_id: UUID,
    body: VisitCardReplyRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> VisitCardReplyEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            reply = await reply_owned_visit_card(
                session,
                card_id=card_id,
                body=body,
                secret=_signing_key(request),
                now=datetime.now(UTC),
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return VisitCardReplyEnvelope(
        data=reply,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/visit-card/uploads",
    response_model=VisitCardUploadEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Issue a short-lived upload URL for a visit card",
)
async def post_visit_card_upload(
    request: Request,
    body: VisitCardUploadRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> VisitCardUploadEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            issued = await issue_visit_card_upload(
                session,
                user_id=user.id,
                body=body,
                secret=_signing_key(request),
                now=datetime.now(UTC),
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return VisitCardUploadEnvelope(
        data=issued,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/visit-cards",
    response_model=VisitFootprintPageEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Page visit footprints for the current spirit",
)
async def get_visit_cards(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
    box: Annotated[Literal["inbox", "outbox"], Query()],
    cursor: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> VisitFootprintPageEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            page = await list_visit_footprints(
                session,
                user_id=user.id,
                box=box,
                cursor=cursor,
                limit=limit,
                secret=_signing_key(request),
                now=datetime.now(UTC),
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return VisitFootprintPageEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/visit-cards/{card_id}/read",
    response_model=VisitCardReadEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Mark a visit footprint as read",
)
async def post_visit_card_read(
    request: Request,
    card_id: UUID,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> VisitCardReadEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            marked = await mark_visit_card_read(session, card_id=card_id)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return VisitCardReadEnvelope(
        data=marked,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
