"""DELETE /account HTTP surface. Spec §14.9. Fixed HTTP 202."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import session_factory_from_app
from app.api.v1.spirit_map import utc_z
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_transaction
from app.schemas.account import (
    AccountDeleteErrorEnvelope,
    AccountDeleteSuccessEnvelope,
    DeleteAccountRequest,
    DeletionResult,
)
from app.services.account import accept_account_deletion

router = APIRouter(prefix="/api/v1", tags=["account"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": AccountDeleteErrorEnvelope, "description": "UNAUTHENTICATED"},
    409: {
        "model": AccountDeleteErrorEnvelope,
        "description": "ACCOUNT_DELETE_PENDING",
    },
    422: {"model": AccountDeleteErrorEnvelope, "description": "INVALID_INPUT"},
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.delete(
    "/account",
    response_model=AccountDeleteSuccessEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Accept account deletion",
    description=(
        "Always returns 202 accepted. iOS must wipe local state immediately. "
        "Worker deletes Storage, then Auth, then verifies DB cascade. "
        "Identity linking is not a business REST endpoint."
    ),
)
async def delete_account(
    request: Request,
    body: DeleteAccountRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> AccountDeleteSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    secret = settings.cursor_hmac_secret
    hmac_secret = (
        secret.get_secret_value() if secret is not None else "kelin-test-account-hash"
    )
    if settings.app_env == "prod" and secret is None:
        raise _unavailable()
    now = datetime.now(UTC)
    async with claimed_transaction(factory, user, allow_account_deleting=True) as session:
        accepted = await accept_account_deletion(
            session,
            user,
            client_id=body.client_id,
            confirm=body.confirm,
            now=now,
            hmac_secret=hmac_secret,
        )
    assert accepted.deletion_id is not None
    assert accepted.requested_at is not None
    return AccountDeleteSuccessEnvelope(
        data=DeletionResult(
            deletion_id=accepted.deletion_id,
            status="accepted",
            requested_at=utc_z(accepted.requested_at),
        ),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
