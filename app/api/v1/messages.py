from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_read_transaction
from app.schemas.messages import MessagePageErrorEnvelope, MessagePageSuccessEnvelope
from app.services.messages import load_message_page

router = APIRouter(prefix="/api/v1", tags=["messages"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": MessagePageErrorEnvelope,
        "description": "UNAUTHENTICATED",
    },
    422: {
        "model": MessagePageErrorEnvelope,
        "description": "INVALID_INPUT or INVALID_CURSOR",
    },
    503: {
        "model": MessagePageErrorEnvelope,
        "description": "DEPENDENCY_UNAVAILABLE",
    },
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.get(
    "/messages",
    response_model=MessagePageSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def get_messages(
    request: Request,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
    cursor: Annotated[
        str | None,
        Query(
            min_length=1,
            description=(
                "Opaque signed keyset cursor. Contains version, sort_time, id, snapshot_at, "
                "and filter_hash. Do not send SQL."
            ),
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50, description="Page size. Default 30, max 50.")] = 30,
) -> MessagePageSuccessEnvelope:
    """Message history.

    Sort is created_at desc, id desc. System messages are omitted. Owner keyset
    pagination uses snapshot_at. Conversation windows are server-owned: Chat
    returns conversation_window_id, under 3 ordinary turns that may be an open window,
    and should_extract=true only when the window is ready. Clients must not submit
    start/end message IDs.
    """
    factory = session_factory_from_app(request.app)
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    try:
        secret = settings.cursor_signing_key()
        async with claimed_read_transaction(factory, _user) as session:
            page = await load_message_page(
                session,
                _user,
                cursor=cursor,
                limit=limit,
                secret=secret,
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return MessagePageSuccessEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
