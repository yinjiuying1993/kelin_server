"""Sight original cleanup. Spec §§11.4, 17.2, 18.5.

Deletes the known object key only. Does not list the bucket. Originals are
removed on expiry/consumed/rejected, with a 24h retention ceiling.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError, public_error_message
from app.core.logging import get_logger, hash_user_id
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.sight_upload import (
    cleanup_reason,
    inspect_object_bytes,
    object_mismatch_reason,
)
from app.integrations.storage import (
    PrivateSightStorage,
    StorageListDenied,
    StorageObjectMissing,
)
from app.repositories import sight_upload as upload_repo

_LOGGER = get_logger(component="sight_cleanup")
_WORKER_ROLE = "kelin_worker"


@dataclass(frozen=True, slots=True)
class CleanupResult:
    upload_id: uuid.UUID
    reason: str
    object_deleted: bool
    object_missing: bool
    verify_reason: str | None
    lag_seconds: int


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    reason: str
    object_deleted: bool


def _api_error(code: str, *, status_code: int) -> ApiError:
    return ApiError(code, public_error_message(code), status_code=status_code)


def _lag_seconds(created_at: datetime, now: datetime) -> int:
    return max(0, int((now - created_at).total_seconds()))


def _assert_no_list(storage: PrivateSightStorage, bucket: str) -> None:
    try:
        storage.service_list(bucket, "")
    except StorageListDenied:
        return
    raise RuntimeError("cleanup must not list the bucket")


async def verify_stored_sight_object(
    storage: PrivateSightStorage,
    row: upload_repo.SightUploadRow,
) -> VerifyResult:
    """Re-check magic/size/hash on an owner-checked key. Mismatch deletes the object."""
    _assert_no_list(storage, row.bucket)
    try:
        body = storage.service_get(row.bucket, row.object_path)
    except StorageObjectMissing:
        return VerifyResult(ok=False, reason="missing", object_deleted=False)
    mismatch = object_mismatch_reason(
        inspect_object_bytes(body),
        expected_size=row.expected_size,
        expected_sha256=row.expected_sha256,
    )
    if mismatch is None:
        return VerifyResult(ok=True, reason="ok", object_deleted=False)
    deleted = storage.service_delete(row.bucket, row.object_path)
    _LOGGER.info(
        "sight_object_verify",
        outcome=mismatch,
        object_deleted=deleted,
        user_id_hash=hash_user_id(row.user_id),
        upload_session_id=str(row.id),
    )
    return VerifyResult(ok=False, reason=mismatch, object_deleted=deleted)


async def handle_upload_cleanup(
    factory: async_sessionmaker[AsyncSession],
    user: CurrentUser,
    upload_id: uuid.UUID,
    *,
    now: datetime,
    storage: PrivateSightStorage,
    db_role: str = _WORKER_ROLE,
) -> CleanupResult:
    async with claimed_transaction(factory, user, db_role=db_role) as session:
        row = await upload_repo.lock_owned_upload(session, owner_id=user.id, upload_id=upload_id)
        if row is None:
            raise _api_error("NOT_FOUND", status_code=404)
        reason = cleanup_reason(
            status=row.status,
            expires_at=row.expires_at,
            created_at=row.created_at,
            object_deleted_at=row.object_deleted_at,
            now=now,
        )
        if reason is None:
            return CleanupResult(
                upload_id=row.id,
                reason="already_clean",
                object_deleted=False,
                object_missing=row.object_deleted_at is not None,
                verify_reason=None,
                lag_seconds=_lag_seconds(row.created_at, now),
            )
        snapshot = row
    _assert_no_list(storage, snapshot.bucket)
    missing = False
    deleted = False
    verify_reason: str | None = None
    try:
        body = storage.service_get(snapshot.bucket, snapshot.object_path)
    except StorageObjectMissing:
        missing = True
    else:
        mismatch = object_mismatch_reason(
            inspect_object_bytes(body),
            expected_size=snapshot.expected_size,
            expected_sha256=snapshot.expected_sha256,
        )
        if mismatch is not None:
            verify_reason = mismatch
        deleted = storage.service_delete(snapshot.bucket, snapshot.object_path)
    async with claimed_transaction(factory, user, db_role=db_role) as session:
        await upload_repo.mark_object_deleted(
            session, owner_id=user.id, upload_id=snapshot.id, now=now
        )
    lag = _lag_seconds(snapshot.created_at, now)
    _LOGGER.info(
        "sight_upload_cleanup",
        reason=reason,
        object_deleted=deleted,
        object_missing=missing,
        verify_reason=verify_reason,
        lag_seconds=lag,
        user_id_hash=hash_user_id(user.id),
        upload_session_id=str(snapshot.id),
        object_path=snapshot.object_path,
    )
    return CleanupResult(
        upload_id=snapshot.id,
        reason=reason,
        object_deleted=deleted,
        object_missing=missing,
        verify_reason=verify_reason,
        lag_seconds=lag,
    )


async def enqueue_due_upload_cleanups(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    now: datetime,
) -> tuple[uuid.UUID, ...]:
    due = await upload_repo.list_due_cleanups(session, owner_id=owner_id, now=now)
    inserted: list[uuid.UUID] = []
    for row in due:
        if await upload_repo.insert_cleanup_outbox(session, owner_id=owner_id, upload_id=row.id):
            inserted.append(row.id)
    return tuple(inserted)
