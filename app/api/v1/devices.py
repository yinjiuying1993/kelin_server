"""POST /devices HTTP surface. Spec §14.6."""

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
from app.schemas.devices import (
    DeviceRegisterErrorEnvelope,
    DeviceRegisterSuccessEnvelope,
    RegisterDeviceRequest,
)
from app.services.devices import register_device

router = APIRouter(prefix="/api/v1", tags=["devices"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": DeviceRegisterErrorEnvelope, "description": "UNAUTHENTICATED"},
    422: {"model": DeviceRegisterErrorEnvelope, "description": "INVALID_INPUT"},
    503: {"model": DeviceRegisterErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


@router.post(
    "/devices",
    response_model=DeviceRegisterSuccessEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
    summary="Register or rotate an APNs device",
    description=(
        "Upsert by installation_id and environment. Token is stored hashed and encrypted. "
        "sandbox and production are not interchangeable. disabled keeps the row. "
        "The response is device_id, enabled, and updated_at only."
    ),
)
async def post_devices(
    request: Request,
    body: RegisterDeviceRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> DeviceRegisterSuccessEnvelope:
    factory = session_factory_from_app(request.app)
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    try:
        async with claimed_transaction(factory, user) as session:
            registration = await register_device(
                session,
                user,
                body,
                now=datetime.now(UTC),
                settings=settings,
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return DeviceRegisterSuccessEnvelope(
        data=registration,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
