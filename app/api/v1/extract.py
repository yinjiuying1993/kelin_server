from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import session_factory_from_app
from app.api.v1.extract_map import extract_result_from_settlement
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.extract import ExtractErrorEnvelope, ExtractRequest, ExtractSuccessEnvelope
from app.services.extract import settle_extract

router = APIRouter(prefix="/api/v1", tags=["extract"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ExtractErrorEnvelope,
        "description": "UNAUTHENTICATED",
    },
    409: {
        "model": ExtractErrorEnvelope,
        "description": "CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {
        "model": ExtractErrorEnvelope,
        "description": "INVALID_INPUT",
    },
    429: {
        "model": ExtractErrorEnvelope,
        "description": "QUOTA_EXCEEDED or RATE_LIMITED",
    },
    503: {
        "model": ExtractErrorEnvelope,
        "description": "MODEL_UNAVAILABLE",
    },
    504: {
        "model": ExtractErrorEnvelope,
        "description": "PROVIDER_TIMEOUT",
    },
}


@router.post(
    "/extract",
    response_model=ExtractSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def post_extract(
    request: Request,
    body: ExtractRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> ExtractSuccessEnvelope:
    """Extract long-term memory from a server-owned conversation window.

    Request is only client_id plus conversation_window_id. Clients must not submit
    start/end message IDs. Repeat on an extracted window returns the original
    memories, style samples, and trait patch. Missing Bailian secret/alias keeps
    the in-process stub. Extract timeout maps to PROVIDER_TIMEOUT.
    """
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    settings = request.app.state.settings
    provider_settings = settings if isinstance(settings, Settings) else None
    async with claimed_transaction(factory, user) as session:
        settled = await settle_extract(session, user, body, now=now, settings=provider_settings)
    return ExtractSuccessEnvelope(
        data=extract_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
