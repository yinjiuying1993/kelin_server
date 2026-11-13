"""POST /feedback. Logged-in user, trimmed text, no list and no reply."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import provider_from_app, session_factory_from_app
from app.core.envelope import request_id_of, utc_server_time
from app.core.security import CurrentUser, require_current_user
from app.schemas.feedback import (
    FeedbackAccepted,
    FeedbackErrorEnvelope,
    FeedbackRequest,
    FeedbackSuccessEnvelope,
)
from app.services.feedback import submit_feedback

router = APIRouter(prefix="/api/v1", tags=["feedback"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": FeedbackErrorEnvelope, "description": "UNAUTHENTICATED"},
    409: {
        "model": FeedbackErrorEnvelope,
        "description": "IDEMPOTENCY_IN_PROGRESS or IDEMPOTENCY_CONFLICT",
    },
    422: {
        "model": FeedbackErrorEnvelope,
        "description": "INVALID_INPUT or MODERATION_REJECTED",
    },
    429: {"model": FeedbackErrorEnvelope, "description": "RATE_LIMITED"},
    503: {"model": FeedbackErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
    504: {"model": FeedbackErrorEnvelope, "description": "PROVIDER_TIMEOUT"},
}


class _TextSafety:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def decision(self, text: str) -> str:
        return await self._provider.moderate_text(text)


@router.post(
    "/feedback",
    response_model=FeedbackSuccessEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Submit feedback",
    description="Stores the trimmed text. The response is only the new id.",
)
async def post_feedback(
    request: Request,
    body: FeedbackRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> FeedbackSuccessEnvelope:
    feedback_id = await submit_feedback(
        session_factory_from_app(request.app),
        user,
        body,
        _TextSafety(provider_from_app(request.app)),
    )
    return FeedbackSuccessEnvelope(
        data=FeedbackAccepted(id=feedback_id),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
