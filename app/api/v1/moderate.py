"""POST /moderate-sight HTTP surface. Spec §11.4."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app, sight_storage_from_app
from app.api.v1.moderate_map import moderate_result_from_settlement
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.schemas.moderate import (
    ModerateSightErrorEnvelope,
    ModerateSightRequest,
    ModerateSightSuccessEnvelope,
)
from app.services.sight_moderate import moderate_sight

router = APIRouter(prefix="/api/v1", tags=["sight"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ModerateSightErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": ModerateSightErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": ModerateSightErrorEnvelope,
        "description": "UPLOAD_NOT_READY, CONFLICT or IDEMPOTENCY_CONFLICT",
    },
    410: {"model": ModerateSightErrorEnvelope, "description": "UPLOAD_EXPIRED"},
    422: {"model": ModerateSightErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": ModerateSightErrorEnvelope, "description": "MODEL_UNAVAILABLE"},
    504: {"model": ModerateSightErrorEnvelope, "description": "PROVIDER_TIMEOUT"},
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.post(
    "/moderate-sight",
    response_model=ModerateSightSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def post_moderate_sight(
    request: Request,
    body: ModerateSightRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> ModerateSightSuccessEnvelope:
    """Re-check owner, object magic/hash, then Safety→Vision→one settlement."""
    factory = session_factory_from_app(request.app)
    storage = sight_storage_from_app(request.app)
    now = datetime.now(UTC)
    settings = request.app.state.settings
    provider_settings = settings if isinstance(settings, Settings) else None
    try:
        settled = await moderate_sight(
            factory,
            user,
            body,
            now=now,
            storage=storage,
            settings=provider_settings,
        )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return ModerateSightSuccessEnvelope(
        data=moderate_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
