from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import session_factory_from_app
from app.api.v1.onboarding_map import onboarding_complete_result_from_settlement
from app.core.envelope import request_id_of, utc_server_time
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.onboarding import (
    CompleteOnboardingRequest,
    OnboardingCompleteErrorEnvelope,
    OnboardingCompleteSuccessEnvelope,
)
from app.services.onboarding import complete_onboarding

router = APIRouter(prefix="/api/v1", tags=["onboarding"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": OnboardingCompleteErrorEnvelope,
        "description": "UNAUTHENTICATED",
    },
    409: {
        "model": OnboardingCompleteErrorEnvelope,
        "description": "ONBOARDING_INCOMPLETE, ONBOARDING_ALREADY_COMPLETED, or CONFLICT",
    },
    422: {
        "model": OnboardingCompleteErrorEnvelope,
        "description": "INVALID_INPUT",
    },
}


@router.post(
    "/onboarding/complete",
    response_model=OnboardingCompleteSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def post_onboarding_complete(
    request: Request,
    body: CompleteOnboardingRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> OnboardingCompleteSuccessEnvelope:
    """Complete onboarding when step=5 with five complete onboarding rounds.

    Repeat with the same client_id returns the same hatched_at.
    Incomplete requests return ONBOARDING_INCOMPLETE.
    """
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    async with claimed_transaction(factory, user) as session:
        settled = await complete_onboarding(session, user, body, now=now)
    return OnboardingCompleteSuccessEnvelope(
        data=onboarding_complete_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
