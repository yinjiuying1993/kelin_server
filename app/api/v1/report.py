"""GET /report and POST /report/line. Spec §§14.7–14.8."""

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
from app.schemas.report import (
    ReportErrorEnvelope,
    ReportLineErrorEnvelope,
    ReportLineRequest,
    ReportLineSuccessEnvelope,
    ReportSuccessEnvelope,
)
from app.services.report import load_report_snapshot
from app.services.report_line import retry_report_line

router = APIRouter(prefix="/api/v1", tags=["report"])

_GET_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ReportErrorEnvelope, "description": "UNAUTHENTICATED"},
    503: {"model": ReportErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}
_LINE_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ReportLineErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": ReportLineErrorEnvelope, "description": "NOT_FOUND"},
    409: {
        "model": ReportLineErrorEnvelope,
        "description": "CONFLICT, IDEMPOTENCY_CONFLICT, or REPORT_LINE_UNAVAILABLE",
    },
    422: {"model": ReportLineErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": ReportLineErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.get(
    "/report",
    response_model=ReportSuccessEnvelope,
    responses=_GET_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Load the seven-day report snapshot",
    description=(
        "Returns the shared ReportSnapshot. Not eligible returns locked without a "
        "business error. The first eligible GET inserts generating and enqueues "
        "report.generate once."
    ),
)
async def get_report(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> ReportSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_transaction(factory, user) as session:
            snapshot = await load_report_snapshot(
                session, user, now=datetime.now(UTC)
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return ReportSuccessEnvelope(
        data=snapshot,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.post(
    "/report/line",
    response_model=ReportLineSuccessEnvelope,
    responses=_LINE_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Retry the report signature line",
    description=(
        "Retries signature_line only when status is partial and the line is null. "
        "client_id plus expected_version. Other card fields stay fixed."
    ),
)
async def post_report_line(
    request: Request,
    body: ReportLineRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> ReportLineSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    try:
        result = await retry_report_line(
            factory,
            user,
            body,
            now=datetime.now(UTC),
            settings=settings,
        )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return ReportLineSuccessEnvelope(
        data=result,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
