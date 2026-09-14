"""DELETE /account accept. Spec §14.9. Always 202 accepted; worker is async."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.account import ACCOUNT_DELETE_CONFIRM
from app.domain.settings import owner_hash
from app.domain.spirit_state import require_aware
from app.repositories import account as account_repo
from app.repositories.account import DeletionAccept


def _api_error(code: str, *, status_code: int, retryable: bool = False) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
    )


async def accept_account_deletion(
    session: AsyncSession,
    user: CurrentUser,
    *,
    client_id,
    confirm: str,
    now: datetime,
    hmac_secret: str,
) -> DeletionAccept:
    require_aware(now, field="now")
    del now
    await account_repo.assert_writable_transaction(session)
    if confirm != ACCOUNT_DELETE_CONFIRM:
        raise _api_error("INVALID_INPUT", status_code=422)
    hashed = owner_hash(user_id=user.id, secret=hmac_secret)
    accepted = await account_repo.accept_account_deletion(
        session, client_id=client_id, owner_hash=hashed
    )
    if accepted.outcome == "pending" or accepted.deletion_id is None:
        raise _api_error("ACCOUNT_DELETE_PENDING", status_code=409)
    return accepted
