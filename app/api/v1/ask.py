"""Ask, helped-row, and public profile HTTP surface."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import provider_from_app, session_factory_from_app
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.schemas.ask import (
    AskErrorEnvelope,
    AskQuestionDetailEnvelope,
    AskQuestionPageEnvelope,
    AskRateEnvelope,
    AskRateRequest,
    AskRecentEnvelope,
    AskSubmitEnvelope,
    AskSubmitRequest,
    AskTurnEnvelope,
    AskTurnRequest,
    AskView,
    HelpedEnvelope,
    SpiritPublicProfileEnvelope,
)
from app.services.ask.gateway import (
    detail_http,
    follow_http,
    helped_http,
    list_http,
    profile_http,
    rate_http,
    recent_http,
    submit_http,
)

router = APIRouter(prefix="/api/v1", tags=["ask"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": AskErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": AskErrorEnvelope, "description": "NOT_FOUND"},
    409: {"model": AskErrorEnvelope, "description": "ASK_CLOSED"},
    422: {"model": AskErrorEnvelope, "description": "INVALID_INPUT or MODERATION_REJECTED"},
}


@router.post(
    "/ask/questions",
    response_model=AskSubmitEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Submit a question",
)
async def submit_ask_question(
    request: Request,
    body: AskSubmitRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> AskSubmitEnvelope:
    result = await _ask_call(
        submit_http(
            _factory(request),
            user,
            _provider(request),
            client_id=body.client_id,
            text=body.text,
            now=datetime.now(UTC),
        )
    )
    return AskSubmitEnvelope(
        data=result,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/ask/questions/recent",
    response_model=AskRecentEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="List the three newest answered questions",
)
async def list_recent_ask_questions(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> AskRecentEnvelope:
    recent = await _ask_call(
        recent_http(_factory(request), user, now=datetime.now(UTC))
    )
    return AskRecentEnvelope(
        data=recent,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/ask/questions",
    response_model=AskQuestionPageEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="List answered questions",
)
async def list_ask_questions(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
    view: Annotated[AskView, Query()] = "all",
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> AskQuestionPageEnvelope:
    page = await _ask_call(
        list_http(
            _factory(request),
            user,
            view=view,
            cursor=cursor,
            secret=_settings(request).cursor_signing_key(),
            now=datetime.now(UTC),
        )
    )
    return AskQuestionPageEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/ask/questions/{question_id}",
    response_model=AskQuestionDetailEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Read one answered question",
)
async def get_ask_question(
    request: Request,
    question_id: UUID,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> AskQuestionDetailEnvelope:
    detail = await _ask_call(
        detail_http(_factory(request), user, question_id=question_id)
    )
    return AskQuestionDetailEnvelope(
        data=detail,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/ask/questions/{question_id}/turns",
    response_model=AskTurnEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Send a follow-up turn",
)
async def send_ask_turn(
    request: Request,
    question_id: UUID,
    body: AskTurnRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> AskTurnEnvelope:
    reply = await _ask_call(
        follow_http(
            _factory(request),
            user,
            _provider(request),
            question_id=question_id,
            client_id=body.client_id,
            text=body.text,
            now=datetime.now(UTC),
        )
    )
    return AskTurnEnvelope(
        data=reply,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/ask/questions/{question_id}/rating",
    response_model=AskRateEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Rate one question once",
)
async def rate_ask_question(
    request: Request,
    question_id: UUID,
    body: AskRateRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> AskRateEnvelope:
    rated = await _ask_call(
        rate_http(
            _factory(request),
            user,
            question_id=question_id,
            client_id=body.client_id,
            rating=body.rating,
        )
    )
    return AskRateEnvelope(
        data=rated,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/ask/helped",
    response_model=HelpedEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Read the owner's single helped row",
)
async def get_ask_helped(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> HelpedEnvelope:
    helped = await _ask_call(helped_http(_factory(request), user))
    return HelpedEnvelope(
        data=helped,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/spirits/{spirit_id}/public",
    response_model=SpiritPublicProfileEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Read a spirit's public profile",
)
async def get_spirit_public_profile(
    request: Request,
    spirit_id: UUID,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SpiritPublicProfileEnvelope:
    profile = await _ask_call(
        profile_http(_factory(request), user, spirit_id=spirit_id)
    )
    return SpiritPublicProfileEnvelope(
        data=profile,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


def _factory(request: Request):  # type: ignore[no-untyped-def]
    return session_factory_from_app(request.app)


def _provider(request: Request):  # type: ignore[no-untyped-def]
    return provider_from_app(request.app)


def _settings(request: Request) -> Settings:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    return settings


async def _ask_call(work):  # type: ignore[no-untyped-def]
    try:
        return await work
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )
