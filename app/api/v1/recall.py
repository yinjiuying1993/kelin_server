"""POST /recall HTTP surface. Spec §§8.7, 11.5, 14.11."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import session_factory_from_app
from app.api.v1.recall_map import recall_result_from_settlement
from app.core.envelope import request_id_of, utc_server_time
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.recall import RecallErrorEnvelope, RecallRequest, RecallSuccessEnvelope
from app.services.recall import recall_spirit

router = APIRouter(prefix="/api/v1", tags=["recall"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": RecallErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": RecallErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": RecallErrorEnvelope,
        "description": "SPIRIT_NOT_LOST, IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {
        "model": RecallErrorEnvelope,
        "description": "INVALID_INPUT or RECALL_SOURCE_INVALID",
    },
    503: {"model": RecallErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}


@router.post(
    "/recall",
    response_model=RecallSuccessEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Recall a lost spirit",
    description=(
        "Food or owned active sight memory. Only when status=lost. "
        "Food quota full still returns home without ordinary food attributes."
    ),
)
async def post_recall(
    request: Request,
    body: RecallRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> RecallSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    async with claimed_transaction(factory, user) as session:
        settled = await recall_spirit(session, user, body, now=now)
    return RecallSuccessEnvelope(
        data=recall_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
