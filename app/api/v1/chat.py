from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import session_factory_from_app
from app.api.v1.chat_map import chat_turn_result_from_settlement
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.chat import ChatErrorEnvelope, ChatRequest, ChatSuccessEnvelope
from app.services.chat import settle_chat_turn

router = APIRouter(prefix="/api/v1", tags=["chat"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": ChatErrorEnvelope,
        "description": "UNAUTHENTICATED",
    },
    409: {
        "model": ChatErrorEnvelope,
        "description": "CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {
        "model": ChatErrorEnvelope,
        "description": "INVALID_INPUT",
    },
    429: {
        "model": ChatErrorEnvelope,
        "description": "QUOTA_EXCEEDED or RATE_LIMITED",
    },
    503: {
        "model": ChatErrorEnvelope,
        "description": "MODEL_UNAVAILABLE",
    },
    504: {
        "model": ChatErrorEnvelope,
        "description": "PROVIDER_TIMEOUT",
    },
}


@router.post(
    "/chat",
    response_model=ChatSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def post_chat(
    request: Request,
    _body: ChatRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> ChatSuccessEnvelope:
    """Chat turn.

    Request onboarding is required and must match server state; onboarding=true
    does not increment ordinary_dialogue_rounds. Only a complete user+spirit pair
    advances onboarding_step. Upstream chat deadline is 18s; timeout maps to
    PROVIDER_TIMEOUT. Missing Bailian secret/alias keeps the in-process stub.
    """
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    settings = request.app.state.settings
    provider_settings = settings if isinstance(settings, Settings) else None
    async with claimed_transaction(factory, _user) as session:
        settled = await settle_chat_turn(session, _user, _body, now=now, settings=provider_settings)
    return ChatSuccessEnvelope(
        data=chat_turn_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
