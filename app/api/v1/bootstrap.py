from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import session_factory_from_app
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_read_transaction, claimed_transaction
from app.schemas.bootstrap import BootstrapErrorEnvelope, BootstrapSuccessEnvelope
from app.services.bootstrap import load_bootstrap_snapshot
from app.services.spirit_state import settle_spirit_state

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
    """Contract-only path. Settle is P06-T02; RR aggregate is T03; HTTP wiring is T04."""
    factory = session_factory_from_app(request.app)
    now = datetime.now(UTC)
    try:
        async with claimed_transaction(factory, _user) as session:
            await settle_spirit_state(session, _user, now=now)
        async with claimed_read_transaction(factory, _user) as session:
            snapshot = await load_bootstrap_snapshot(session, _user, now=now)
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return BootstrapSuccessEnvelope(
        data=snapshot,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
