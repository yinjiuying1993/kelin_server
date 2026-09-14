"""Sight upload-url HTTP surface. Spec §11.3."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.storage import (
    SightUploadErrorEnvelope,
    SightUploadSuccessEnvelope,
    SightUploadUrlRequest,
)
from app.services.sight_upload import issue_sight_upload_url

router = APIRouter(prefix="/api/v1", tags=["storage"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": SightUploadErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": SightUploadErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": SightUploadErrorEnvelope,
        "description": "CONFLICT, IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    410: {"model": SightUploadErrorEnvelope, "description": "UPLOAD_EXPIRED"},
    422: {"model": SightUploadErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": SightUploadErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
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


@router.post(
    "/storage/sight-upload-url",
    response_model=SightUploadSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def post_sight_upload_url(
    request: Request,
    body: SightUploadUrlRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SightUploadSuccessEnvelope:
    """Issue a short-lived single-object PUT URL. Path is server-generated."""
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, user) as session:
            issued = await issue_sight_upload_url(
                session,
                user,
                body,
                now=now,
                signing_key=_signing_key(request),
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return SightUploadSuccessEnvelope(
        data=issued,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
