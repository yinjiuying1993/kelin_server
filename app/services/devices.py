"""Register APNs devices. Spec §§14.6, 14.11.

Token is hashed and encrypted before persistence. Logs never include the token.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, public_error_message
from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.domain.bootstrap import utc_z
from app.domain.devices import (
    encrypt_apns_token,
    environment_is_compatible,
    hash_apns_token,
)
from app.domain.spirit_state import require_aware
from app.repositories import devices as device_repo
from app.schemas.devices import DeviceRegistration, RegisterDeviceRequest

_LOGGER = get_logger(component="devices")


def _invalid_input() -> ApiError:
    return ApiError(
        "INVALID_INPUT",
        public_error_message("INVALID_INPUT"),
        status_code=422,
    )


async def register_device(
    session: AsyncSession,
    user: CurrentUser,
    body: RegisterDeviceRequest,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> DeviceRegistration:
    resolved = settings or get_settings()
    moment = require_aware(now or datetime.now(UTC), field="now")
    if not environment_is_compatible(resolved.app_env, body.environment):
        raise _invalid_input()
    await device_repo.assert_writable_transaction(session)
    token_hash = hash_apns_token(body.apns_token)
    cipher = encrypt_apns_token(body.apns_token, resolved.device_token_fernet_key())
    try:
        row = await device_repo.upsert_device(
            session,
            installation_id=body.installation_id,
            token_hash=token_hash,
            token_encrypted=cipher,
            environment=body.environment,
            enabled=body.enabled,
            app_version=body.app_version,
            locale=body.locale,
            now=moment,
        )
    except DBAPIError as exc:
        raise _invalid_input() from exc
    _LOGGER.info(
        "device_registered",
        device_id=str(row.device_id),
        environment=body.environment,
        enabled=row.enabled,
    )
    return DeviceRegistration(
        device_id=row.device_id,
        enabled=row.enabled,
        updated_at=utc_z(row.updated_at),
    )
