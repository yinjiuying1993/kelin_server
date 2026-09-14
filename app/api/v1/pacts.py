"""Pact HTTP surface. Spec §§13.1–13.4.

P15-T02 wires POST /pacts. T03 wires POST /pact-session. T04–T05 wire POST /pact-answer.
T06 wires POST /pact-skip. Last-question finalize is inside the last missing answer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app
from app.api.v1.pact_map import (
    answer_result_from_settlement,
    create_result_from_settlement,
    session_result_from_settlement,
    skip_result_from_settlement,
)
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.pact import (
    CreatePactRequest,
    CreatePactSuccessEnvelope,
    PactAnswerRequest,
    PactAnswerSuccessEnvelope,
    PactErrorEnvelope,
    PactSessionRequest,
    PactSessionSuccessEnvelope,
    PactSkipRequest,
    PactSkipSuccessEnvelope,
)
from app.services.pact import create_pact as settle_create_pact
from app.services.pact import open_pact_session as settle_open_session
from app.services.pact import submit_pact_answer as settle_submit_answer
from app.services.pact import submit_pact_skip as settle_submit_skip

router = APIRouter(prefix="/api/v1", tags=["pact"])

_CREATE_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": PactErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": PactErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": PactErrorEnvelope,
        "description": "PACT_ALREADY_ACTIVE, IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {"model": PactErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": PactErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}

_SESSION_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": PactErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": PactErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": PactErrorEnvelope,
        "description": "PACT_DAY_CLOSED, CONFLICT, IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS",
    },
    422: {"model": PactErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": PactErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}

_ANSWER_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": PactErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {
        "model": PactErrorEnvelope,
        "description": "NOT_FOUND or PACT_QUESTION_NOT_FOUND",
    },
    409: {
        "model": PactErrorEnvelope,
        "description": (
            "CONFLICT, PACT_QUESTION_ALREADY_ANSWERED, "
            "IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS"
        ),
    },
    422: {"model": PactErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": PactErrorEnvelope, "description": "MODEL_UNAVAILABLE"},
}

_SKIP_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": PactErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": PactErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": PactErrorEnvelope,
        "description": (
            "PACT_SESSION_ALREADY_SUBMITTED, PACT_DAY_CLOSED, "
            "IDEMPOTENCY_CONFLICT or IDEMPOTENCY_IN_PROGRESS"
        ),
    },
    422: {"model": PactErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": PactErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.post(
    "/pacts",
    response_model=CreatePactSuccessEnvelope,
    status_code=status.HTTP_200_OK,
    responses=_CREATE_ERRORS,
    summary="Create an interview or notes pact",
    description=(
        "Creates one active pact per spirit. theme is interview or notes. "
        "notes_memory_id must be an owned active knowledge memory when theme is notes. "
        "Clients must not send score, completeness, mark, or finalized. "
        "A second active pact is PACT_ALREADY_ACTIVE. There is no complete route."
    ),
)
async def create_pact(
    request: Request,
    body: CreatePactRequest,
    user: CurrentUser = Depends(require_current_user),
) -> CreatePactSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, user) as session:
            settled = await settle_create_pact(session, user, body, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return CreatePactSuccessEnvelope(
        data=create_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/pact-session",
    response_model=PactSessionSuccessEnvelope,
    status_code=status.HTTP_200_OK,
    responses=_SESSION_ERRORS,
    summary="Open today's pact session",
    description=(
        "Server timezone/day is authoritative. Same day returns the same session. "
        "Resource has explain, exactly 3 questions, question_bank_version, and row_version. "
        "Closed week is PACT_DAY_CLOSED. Device clocks cannot change session_date."
    ),
)
async def open_pact_session(
    request: Request,
    body: PactSessionRequest,
    user: CurrentUser = Depends(require_current_user),
) -> PactSessionSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, user) as session:
            settled = await settle_open_session(session, user, body, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return PactSessionSuccessEnvelope(
        data=session_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/pact-answer",
    response_model=PactAnswerSuccessEnvelope,
    status_code=status.HTTP_200_OK,
    responses=_ANSWER_ERRORS,
    summary="Submit one pact answer",
    description=(
        "Each question has a stable client_id. The first two answers return feedback and "
        "answered_count without finalized. The last missing answer finalizes inside this "
        "request: finalized=true, score, pact_status, mistakes, and a pact patch. "
        "Unknown question_id is PACT_QUESTION_NOT_FOUND. A second payload for the same "
        "question is PACT_QUESTION_ALREADY_ANSWERED. Clients must not send score or mark. "
        "There is no POST /pacts/{id}/complete."
    ),
)
async def submit_pact_answer(
    request: Request,
    body: PactAnswerRequest,
    user: CurrentUser = Depends(require_current_user),
) -> PactAnswerSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, user) as session:
            settled = await settle_submit_answer(session, user, body, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return PactAnswerSuccessEnvelope(
        data=answer_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/pact-skip",
    response_model=PactSkipSuccessEnvelope,
    status_code=status.HTTP_200_OK,
    responses=_SKIP_ERRORS,
    summary="Skip today's pact session",
    description=(
        "Idempotent per client_id and session_date. Skip is not a completed session and "
        "does not raise completed_sessions. An already answered day is "
        "PACT_SESSION_ALREADY_SUBMITTED."
    ),
)
async def skip_pact_session(
    request: Request,
    body: PactSkipRequest,
    user: CurrentUser = Depends(require_current_user),
) -> PactSkipSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, user) as session:
            settled = await settle_submit_skip(session, user, body, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return PactSkipSuccessEnvelope(
        data=skip_result_from_settlement(settled),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
