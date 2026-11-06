from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.bootstrap import BootstrapErrorEnvelope, BootstrapSuccessEnvelope
from app.services.bootstrap import run_bootstrap_snapshot

router = APIRouter(prefix="/api/v1", tags=["bootstrap"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": BootstrapErrorEnvelope,
        "description": "UNAUTHENTICATED",
    },
    503: {
        "model": BootstrapErrorEnvelope,
        "description": "DEPENDENCY_UNAVAILABLE",
    },
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.get(
    "/bootstrap",
    response_model=BootstrapSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def get_bootstrap(
    request: Request,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> BootstrapSuccessEnvelope:
    """Lock → settle(now) via facade → assemble room in one claimed write tx."""
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, _user) as session:
            snapshot = await run_bootstrap_snapshot(session, _user, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return BootstrapSuccessEnvelope(
        data=snapshot,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
