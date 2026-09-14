"""Issue short-lived sight PUT URLs. Spec §§8.4, 11.3.

Path is always server-generated. Clients never receive a service role.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.logging import get_logger, hash_user_id
from app.core.security import CurrentUser
from app.domain.cursor import utc_iso
from app.domain.sight_upload import (
    ALLOWED_MIME,
    MAX_SIZE_BYTES,
    PUT_METHOD,
    SIGHT_BUCKET,
    InvalidSightUpload,
    assert_server_object_path,
    expires_at_for,
    sight_object_path,
    sight_upload_request_hash,
    sign_put_url,
    validate_upload_constraints,
)
from app.repositories import feed as feed_repo
from app.repositories import sight_upload as upload_repo
from app.schemas.storage import SightUploadUrlRequest, SightUploadUrlResult, put_headers

_LOGGER = get_logger(component="sight_upload")


def _api_error(
    code: str,
    *,
    status_code: int,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
        details=details,
    )


def _payload_source(payload: object) -> str | None:
    if isinstance(payload, dict):
        source = payload.get("source")
        return str(source) if source is not None else None
    return None


def _is_pending_photo(feed: feed_repo.FeedRow) -> bool:
    return (
        feed.kind == "sight"
        and feed.status == "pending"
        and _payload_source(feed.payload) == "photo"
    )


def _session_result(
    row: upload_repo.SightUploadRow,
    *,
    signing_key: bytes,
) -> SightUploadUrlResult:
    assert_server_object_path(
        row.object_path,
        user_id=row.user_id,
        feed_id=row.feed_id,
        upload_id=row.id,
    )
    url = sign_put_url(
        bucket=row.bucket,
        object_path=row.object_path,
        expires_at=row.expires_at,
        secret=signing_key,
    )
    return SightUploadUrlResult(
        upload_session_id=row.id,
        method=PUT_METHOD,
        url=url,
        headers=put_headers(),
        object_path=row.object_path,
        expires_at=utc_iso(row.expires_at),
        max_size_bytes=MAX_SIZE_BYTES,
    )


def _reject_stale_claim(claim: feed_repo.IdempotencyClaim, expected_hash: str) -> None:
    if claim.request_hash != expected_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.status == "conflict":
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status != "completed":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)


async def _load_claimed_session(
    session: AsyncSession,
    user: CurrentUser,
    claim: feed_repo.IdempotencyClaim,
    client_id: uuid.UUID,
) -> upload_repo.SightUploadRow:
    row = None
    if claim.resource_id is not None:
        row = await upload_repo.fetch_by_id(session, owner_id=user.id, upload_id=claim.resource_id)
    if row is None:
        row = await upload_repo.fetch_by_client_id(session, owner_id=user.id, client_id=client_id)
    if row is None:
        raise _api_error("NOT_FOUND", status_code=404)
    return row


async def _replay_or_expire(
    session: AsyncSession,
    user: CurrentUser,
    row: upload_repo.SightUploadRow,
    *,
    now: datetime,
    signing_key: bytes,
) -> SightUploadUrlResult:
    if row.status == "expired" or row.expires_at <= now:
        if row.status != "expired":
            await upload_repo.mark_expired(session, owner_id=user.id, upload_id=row.id)
        raise _api_error("UPLOAD_EXPIRED", status_code=410)
    if row.status in {"consumed", "rejected"}:
        raise _api_error("UPLOAD_EXPIRED", status_code=410)
    return _session_result(row, signing_key=signing_key)


async def issue_sight_upload_url(
    session: AsyncSession,
    user: CurrentUser,
    body: SightUploadUrlRequest,
    *,
    now: datetime,
    signing_key: bytes,
) -> SightUploadUrlResult:
    await feed_repo.assert_writable_transaction(session)
    try:
        validate_upload_constraints(
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
            sha256=body.sha256,
        )
    except InvalidSightUpload as exc:
        raise _api_error("INVALID_INPUT", status_code=422) from exc
    request_hash = sight_upload_request_hash(
        feed_id=body.feed_id,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
        sha256=body.sha256,
    )
    locked = await feed_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    feed = await feed_repo.lock_owned_feed(session, owner_id=user.id, feed_id=body.feed_id)
    if feed is None or not _is_pending_photo(feed):
        raise _api_error("NOT_FOUND", status_code=404)
    claim = await upload_repo.claim_upload_idempotency(
        session, user.id, body.client_id, request_hash
    )
    if not claim.inserted:
        _reject_stale_claim(claim, request_hash)
        existing = await _load_claimed_session(session, user, claim, body.client_id)
        return await _replay_or_expire(session, user, existing, now=now, signing_key=signing_key)
    active = await upload_repo.lock_active_for_feed(session, owner_id=user.id, feed_id=feed.id)
    if any(item.status in {"uploaded", "verifying"} for item in active):
        raise _api_error("CONFLICT", status_code=409)
    await upload_repo.expire_issued_for_feed(
        session, owner_id=user.id, feed_id=feed.id, except_client_id=body.client_id
    )
    upload_id = uuid.uuid4()
    object_path = sight_object_path(user.id, feed.id, upload_id)
    assert_server_object_path(object_path, user_id=user.id, feed_id=feed.id, upload_id=upload_id)
    expires_at = expires_at_for(now)
    row = await upload_repo.insert_issued(
        session,
        upload_id=upload_id,
        owner_id=user.id,
        spirit_id=locked.id,
        feed_id=feed.id,
        client_id=body.client_id,
        object_path=object_path,
        size_bytes=body.size_bytes,
        sha256=body.sha256,
        expires_at=expires_at,
        created_at=now,
    )
    await upload_repo.complete_upload_idempotency(session, user.id, body.client_id, row.id)
    _LOGGER.info(
        "sight_upload_url_issued",
        user_id_hash=hash_user_id(user.id),
        upload_session_id=str(row.id),
        mime=ALLOWED_MIME,
        bucket=SIGHT_BUCKET,
    )
    return _session_result(row, signing_key=signing_key)
