"""List, add, and remove undirected friend edges. Spec §§8.9, 14.1–14.3."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.cursor import (
    CURSOR_VERSION,
    InvalidCursor,
    MessageCursor,
    decode_friend_cursor,
    encode_friend_cursor,
    friends_filter_hash,
    utc_iso,
)
from app.domain.friends import (
    FRIEND_ADD_OPERATION,
    FRIEND_ADDED_EVENT,
    FRIEND_REMOVE_OPERATION,
    FRIEND_REMOVED_EVENT,
    friend_add_hash,
    friend_remove_hash,
)
from app.repositories import friends as friend_repo
from app.repositories.friends import FriendPublicRow, IdempotencyClaim
from app.schemas.social_api import (
    AddFriendRequest,
    FriendPage,
    FriendPublic,
    PublicSpiritProfile,
)
from app.schemas.spirit import MutationEvent, SpiritStage, SpiritStatus


@dataclass(frozen=True, slots=True)
class FriendAddSettlement:
    snapshot_version: int
    edge: FriendPublicRow
    events: tuple[MutationEvent, ...]


@dataclass(frozen=True, slots=True)
class FriendRemoveSettlement:
    snapshot_version: int
    friend_id: UUID
    events: tuple[MutationEvent, ...]


def _api_error(
    code: str,
    *,
    status_code: int,
    retryable: bool = False,
) -> ApiError:
    return ApiError(
        code,
        public_error_message(code),
        status_code=status_code,
        retryable=retryable,
    )


def _invalid_cursor() -> ApiError:
    return _api_error("INVALID_CURSOR", status_code=422)


def _raise_definer_error(exc: DBAPIError) -> None:
    sqlstate, message = friend_repo.friend_definer_sqlstate(exc)
    if sqlstate == "P0001" or "SELF_FRIEND_NOT_ALLOWED" in message:
        raise _api_error("SELF_FRIEND_NOT_ALLOWED", status_code=409) from exc
    if sqlstate == "P0002" or "NOT_FOUND" in message:
        raise _api_error("NOT_FOUND", status_code=404) from exc
    raise exc


def _stage(value: str) -> SpiritStage:
    if value not in {"whelp", "formed", "awake"}:
        raise RuntimeError("unexpected spirit stage")
    return cast(SpiritStage, value)


def _status(value: str) -> SpiritStatus:
    if value not in {"home", "away", "study", "lost"}:
        raise RuntimeError("unexpected spirit status")
    return cast(SpiritStatus, value)


def public_friend_from_row(row: FriendPublicRow) -> FriendPublic:
    return FriendPublic(
        friend_id=row.friend_id,
        spirit=PublicSpiritProfile(
            id=row.peer_id,
            title=row.title,
            stage=_stage(row.stage),
            public_marks=list(row.public_marks),
            status=_status(row.status),
        ),
        created_at=utc_iso(row.created_at),
    )


async def load_friend_page(
    session: AsyncSession,
    user: CurrentUser,
    *,
    cursor: str | None,
    limit: int,
    secret: bytes,
) -> FriendPage:
    filter_hash = friends_filter_hash(user.id)
    cursor_time = None
    cursor_id = None
    if cursor is None:
        snapshot_at = await friend_repo.fetch_transaction_now(session)
    else:
        try:
            decoded = decode_friend_cursor(
                cursor,
                secret=secret,
                expected_filter_hash=filter_hash,
            )
        except InvalidCursor as exc:
            raise _invalid_cursor() from exc
        snapshot_at = decoded.snapshot_at
        cursor_time = decoded.sort_time
        cursor_id = decoded.id
    spirit_id = await friend_repo.fetch_owned_spirit_id(session, user.id)
    if spirit_id is None:
        return FriendPage(
            items=[],
            next_cursor=None,
            has_more=False,
            snapshot_at=utc_iso(snapshot_at),
        )
    rows = await friend_repo.list_friends_keyset(
        session,
        owner_spirit_id=spirit_id,
        snapshot_at=snapshot_at,
        fetch_limit=limit + 1,
        cursor_time=cursor_time,
        cursor_id=cursor_id,
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = None
    if has_more:
        last = page_rows[-1]
        next_cursor = encode_friend_cursor(
            MessageCursor(
                version=CURSOR_VERSION,
                sort_time=last.created_at,
                id=last.friend_id,
                snapshot_at=snapshot_at,
                filter_hash=filter_hash,
            ),
            secret=secret,
        )
    return FriendPage(
        items=[public_friend_from_row(row) for row in page_rows],
        next_cursor=next_cursor,
        has_more=has_more,
        snapshot_at=utc_iso(snapshot_at),
    )


async def add_owned_friend(
    session: AsyncSession,
    user: CurrentUser,
    body: AddFriendRequest,
    *,
    now: datetime,
) -> FriendAddSettlement:
    await friend_repo.assert_writable_transaction(session)
    locked = await friend_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = friend_add_hash(invite_code=body.invite_code)
    claim = await friend_repo.claim_friend_idempotency(
        session,
        user.id,
        body.client_id,
        request_hash,
        operation=FRIEND_ADD_OPERATION,
    )
    if not claim.inserted:
        return await _replay_add(session, user, locked, claim, request_hash, now)
    try:
        friend_id = await friend_repo.add_friend_by_code(session, body.invite_code)
    except DBAPIError as exc:
        _raise_definer_error(exc)
        raise
    edge = await friend_repo.fetch_friend_public(session, friend_id)
    if edge is None:
        raise _api_error("NOT_FOUND", status_code=404)
    snapshot = await friend_repo.bump_spirit_version(
        session, owner_id=user.id, spirit_id=locked.id
    )
    await friend_repo.complete_friend_idempotency(
        session,
        user.id,
        body.client_id,
        friend_id,
        operation=FRIEND_ADD_OPERATION,
    )
    return FriendAddSettlement(
        snapshot_version=snapshot,
        edge=edge,
        events=(
            MutationEvent(id=uuid.uuid4(), type=FRIEND_ADDED_EVENT, occurred_at=utc_iso(now)),
        ),
    )


async def remove_owned_friend(
    session: AsyncSession,
    user: CurrentUser,
    *,
    friend_id: UUID,
    client_id: UUID,
    now: datetime,
) -> FriendRemoveSettlement:
    await friend_repo.assert_writable_transaction(session)
    locked = await friend_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = friend_remove_hash(friend_id=friend_id)
    claim = await friend_repo.claim_friend_idempotency(
        session,
        user.id,
        client_id,
        request_hash,
        operation=FRIEND_REMOVE_OPERATION,
    )
    if not claim.inserted:
        return await _replay_remove(session, locked, claim, request_hash, friend_id, now)
    removed = await friend_repo.remove_friend_edge(session, friend_id)
    if removed is None:
        raise _api_error("NOT_FOUND", status_code=404)
    snapshot = await friend_repo.bump_spirit_version(
        session, owner_id=user.id, spirit_id=locked.id
    )
    await friend_repo.complete_friend_idempotency(
        session,
        user.id,
        client_id,
        removed,
        operation=FRIEND_REMOVE_OPERATION,
    )
    return FriendRemoveSettlement(
        snapshot_version=snapshot,
        friend_id=removed,
        events=(
            MutationEvent(
                id=uuid.uuid4(), type=FRIEND_REMOVED_EVENT, occurred_at=utc_iso(now)
            ),
        ),
    )


async def _replay_add(
    session: AsyncSession,
    user: CurrentUser,
    locked: friend_repo.SpiritLock,
    claim: IdempotencyClaim,
    request_hash: str,
    now: datetime,
) -> FriendAddSettlement:
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    if claim.resource_id is None:
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    edge = await friend_repo.fetch_friend_public(session, claim.resource_id)
    if edge is None:
        raise _api_error("NOT_FOUND", status_code=404)
    del user
    return FriendAddSettlement(
        snapshot_version=locked.version,
        edge=edge,
        events=(
            MutationEvent(id=uuid.uuid4(), type=FRIEND_ADDED_EVENT, occurred_at=utc_iso(now)),
        ),
    )


async def _replay_remove(
    session: AsyncSession,
    locked: friend_repo.SpiritLock,
    claim: IdempotencyClaim,
    request_hash: str,
    friend_id: UUID,
    now: datetime,
) -> FriendRemoveSettlement:
    del session
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    resource_id = claim.resource_id if claim.resource_id is not None else friend_id
    return FriendRemoveSettlement(
        snapshot_version=locked.version,
        friend_id=resource_id,
        events=(
            MutationEvent(
                id=uuid.uuid4(), type=FRIEND_REMOVED_EVENT, occurred_at=utc_iso(now)
            ),
        ),
    )
