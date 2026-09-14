"""Friends and postcards HTTP surface. Spec §§14.1–14.5.

P16-T01 locks OpenAPI, pagination, events, and the public whitelist.
T02 wires friend edges. T03 wires postcard list/read. Visit plan/settle
remain worker-only; clients must not select host or NPC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app
from app.api.v1.social_map import (
    add_friend_from_settlement,
    read_postcard_from_settlement,
    remove_friend_from_settlement,
)
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_read_transaction, claimed_transaction
from app.schemas.social_api import (
    AddFriendRequest,
    AddFriendSuccessEnvelope,
    FriendListQuery,
    FriendPageSuccessEnvelope,
    PostcardListQuery,
    PostcardPageSuccessEnvelope,
    PostcardReadRequest,
    PostcardReadSuccessEnvelope,
    RemoveFriendRequest,
    RemoveFriendSuccessEnvelope,
    SocialErrorEnvelope,
)
from app.services.friends import add_owned_friend, load_friend_page, remove_owned_friend
from app.services.postcards import load_postcard_page, read_owned_postcard

router = APIRouter(prefix="/api/v1", tags=["social"])

_LIST_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SocialErrorEnvelope, "description": "UNAUTHENTICATED"},
    422: {
        "model": SocialErrorEnvelope,
        "description": "INVALID_INPUT or INVALID_CURSOR",
    },
    503: {"model": SocialErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}

_ADD_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SocialErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": SocialErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": SocialErrorEnvelope,
        "description": (
            "SELF_FRIEND_NOT_ALLOWED, FRIEND_NOT_ALLOWED, "
            "IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS"
        ),
    },
    422: {"model": SocialErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": SocialErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}

_REMOVE_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SocialErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": SocialErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": SocialErrorEnvelope,
        "description": "IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {"model": SocialErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": SocialErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}

_READ_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SocialErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": SocialErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": SocialErrorEnvelope,
        "description": "IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {"model": SocialErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": SocialErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.get(
    "/friends",
    response_model=FriendPageSuccessEnvelope,
    responses=_LIST_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="List friends",
    description=(
        "Keyset page of friend edges. Public profile is title, stage, public_marks, "
        "and status only. Never user_id, avatar, conversation, memory, or location. "
        "INVALID_CURSOR when the cursor or filter hash is stale. "
        "Default limit 30, maximum 50."
    ),
)
async def list_friends(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
    cursor: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> FriendPageSuccessEnvelope:
    FriendListQuery(cursor=cursor, limit=limit)
    factory = session_factory_from_app(request.app)
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    try:
        secret = settings.cursor_signing_key()
        async with claimed_read_transaction(factory, user) as session:
            page = await load_friend_page(
                session,
                user,
                cursor=cursor,
                limit=limit,
                secret=secret,
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return FriendPageSuccessEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/friends",
    response_model=AddFriendSuccessEnvelope,
    responses=_ADD_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Add a friend by invite code",
    description=(
        "Trim and uppercase invite_code, then insert the undirected low<high edge. "
        "Adding yourself is SELF_FRIEND_NOT_ALLOWED. Unknown codes are NOT_FOUND. "
        "Repeat add of the same edge is idempotent. Clients do not pick destinations. "
        "Returns the friend edge, public spirit profile, and social patch."
    ),
)
async def add_friend(
    request: Request,
    body: AddFriendRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> AddFriendSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, user) as session:
            settled = await add_owned_friend(session, user, body, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return AddFriendSuccessEnvelope(
        data=add_friend_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.delete(
    "/friends/{friend_id}",
    response_model=RemoveFriendSuccessEnvelope,
    responses=_REMOVE_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Remove a friend edge",
    description=(
        "friend_id is the edge id. Either participant may remove. Historical postcards "
        "are kept. In-flight visits may finish but no new visit is planned. NOT_FOUND "
        "if the caller is not a participant."
    ),
)
async def remove_friend(
    request: Request,
    friend_id: UUID,
    body: RemoveFriendRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> RemoveFriendSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, user) as session:
            settled = await remove_owned_friend(
                session,
                user,
                friend_id=friend_id,
                client_id=body.client_id,
                now=now,
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return RemoveFriendSuccessEnvelope(
        data=remove_friend_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/postcards",
    response_model=PostcardPageSuccessEnvelope,
    responses=_LIST_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="List received postcards",
    description=(
        "Receiver-only keyset page. unread_only filters read_at is null. "
        "Each item includes public sender or NPC profile and the settled visit "
        "whitelist context. Changing unread_only invalidates an old cursor. "
        "INVALID_CURSOR on a stale cursor. Default limit 30, maximum 50."
    ),
)
async def list_postcards(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
    unread_only: Annotated[bool, Query()] = False,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> PostcardPageSuccessEnvelope:
    PostcardListQuery(unread_only=unread_only, cursor=cursor, limit=limit)
    factory = session_factory_from_app(request.app)
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    try:
        secret = settings.cursor_signing_key()
        async with claimed_read_transaction(factory, user) as session:
            page = await load_postcard_page(
                session,
                user,
                unread_only=unread_only,
                cursor=cursor,
                limit=limit,
                secret=secret,
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return PostcardPageSuccessEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.patch(
    "/postcards/{postcard_id}/read",
    response_model=PostcardReadSuccessEnvelope,
    responses=_READ_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Mark a postcard read",
    description=(
        "Receiver only. First write of read_at uses server time; replay returns the "
        "same timestamp. Non-receiver is NOT_FOUND. Clients must roll back the "
        "unread badge if this call fails."
    ),
)
async def read_postcard(
    request: Request,
    postcard_id: UUID,
    body: PostcardReadRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> PostcardReadSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, user) as session:
            settled = await read_owned_postcard(
                session,
                user,
                postcard_id=postcard_id,
                client_id=body.client_id,
                now=now,
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return PostcardReadSuccessEnvelope(
        data=read_postcard_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
