"""List received postcards and mark them read. Spec §§14.4–14.5."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.cursor import (
    CURSOR_VERSION,
    InvalidCursor,
    MessageCursor,
    decode_postcard_cursor,
    encode_postcard_cursor,
    postcards_filter_hash,
    utc_iso,
)
from app.domain.postcards import (
    POSTCARD_READ_EVENT,
    POSTCARD_READ_OPERATION,
    postcard_read_hash,
)
from app.repositories import postcards as postcard_repo
from app.repositories.postcards import IdempotencyClaim, PostcardPublicRow
from app.schemas.social_api import (
    NpcPublicProfile,
    PostcardPage,
    PostcardPublic,
    PublicSpiritProfile,
    PublicVisitContext,
    VisitPublic,
)
from app.schemas.spirit import MutationEvent


@dataclass(frozen=True, slots=True)
class PostcardReadSettlement:
    snapshot_version: int
    postcard: PostcardPublic
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


def public_postcard_from_row(row: PostcardPublicRow) -> PostcardPublic:
    return PostcardPublic(
        id=row.postcard_id,
        visit_id=row.visit_id,
        sender=None if row.sender is None else PublicSpiritProfile.model_validate(row.sender),
        npc=None if row.npc is None else NpcPublicProfile.model_validate(row.npc),
        text=row.body,
        read_at=None if row.read_at is None else utc_iso(row.read_at),
        created_at=utc_iso(row.created_at),
        visit=_visit_from_payload(row.visit),
    )


def _visit_from_payload(payload: dict[str, Any]) -> VisitPublic:
    context = payload.get("public_context")
    if not isinstance(context, dict):
        raise RuntimeError("visit public_context is required")
    marks = context.get("public_marks") or []
    if not isinstance(marks, list):
        marks = []
    host = payload.get("host")
    npc = payload.get("npc")
    return VisitPublic(
        id=UUID(str(payload["id"])),
        plan_id=UUID(str(payload["plan_id"])),
        destination_index=cast(Any, int(payload["destination_index"])),
        status=cast(Any, str(payload["status"])),
        host=None if host is None else PublicSpiritProfile.model_validate(host),
        npc=None if npc is None else NpcPublicProfile.model_validate(npc),
        public_context=PublicVisitContext(
            title=str(context["title"]),
            stage=cast(Any, str(context["stage"])),
            weather=str(context["weather"]),
            public_marks=[str(item) for item in marks],
        ),
    )


async def load_postcard_page(
    session: AsyncSession,
    user: CurrentUser,
    *,
    unread_only: bool,
    cursor: str | None,
    limit: int,
    secret: bytes,
) -> PostcardPage:
    filter_hash = postcards_filter_hash(user.id, unread_only=unread_only)
    cursor_time = None
    cursor_id = None
    if cursor is None:
        snapshot_at = await postcard_repo.fetch_transaction_now(session)
    else:
        try:
            decoded = decode_postcard_cursor(
                cursor,
                secret=secret,
                expected_filter_hash=filter_hash,
            )
        except InvalidCursor as exc:
            raise _invalid_cursor() from exc
        snapshot_at = decoded.snapshot_at
        cursor_time = decoded.sort_time
        cursor_id = decoded.id
    spirit_id = await postcard_repo.fetch_owned_spirit_id(session, user.id)
    if spirit_id is None:
        return PostcardPage(
            items=[],
            next_cursor=None,
            has_more=False,
            snapshot_at=utc_iso(snapshot_at),
        )
    rows = await postcard_repo.list_postcards_keyset(
        session,
        owner_spirit_id=spirit_id,
        unread_only=unread_only,
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
        next_cursor = encode_postcard_cursor(
            MessageCursor(
                version=CURSOR_VERSION,
                sort_time=last.created_at,
                id=last.postcard_id,
                snapshot_at=snapshot_at,
                filter_hash=filter_hash,
            ),
            secret=secret,
        )
    return PostcardPage(
        items=[public_postcard_from_row(row) for row in page_rows],
        next_cursor=next_cursor,
        has_more=has_more,
        snapshot_at=utc_iso(snapshot_at),
    )


async def read_owned_postcard(
    session: AsyncSession,
    user: CurrentUser,
    *,
    postcard_id: UUID,
    client_id: UUID,
    now: datetime,
) -> PostcardReadSettlement:
    await postcard_repo.assert_writable_transaction(session)
    locked = await postcard_repo.lock_owned_spirit(session, user.id)
    if locked is None:
        raise _api_error("NOT_FOUND", status_code=404)
    request_hash = postcard_read_hash(postcard_id=postcard_id)
    claim = await postcard_repo.claim_postcard_idempotency(
        session,
        user.id,
        client_id,
        request_hash,
        operation=POSTCARD_READ_OPERATION,
    )
    if not claim.inserted:
        return await _replay_read(session, locked, claim, request_hash, postcard_id, now)
    wrote = await postcard_repo.mark_postcard_read(
        session, spirit_id=locked.id, postcard_id=postcard_id
    )
    row = await postcard_repo.fetch_postcard_public(session, postcard_id)
    if row is None or row.read_at is None:
        raise _api_error("NOT_FOUND", status_code=404)
    snapshot = locked.version
    if wrote is not None:
        snapshot = await postcard_repo.bump_spirit_version(
            session, owner_id=user.id, spirit_id=locked.id
        )
    await postcard_repo.complete_postcard_idempotency(
        session,
        user.id,
        client_id,
        postcard_id,
        operation=POSTCARD_READ_OPERATION,
    )
    postcard = public_postcard_from_row(row)
    return PostcardReadSettlement(
        snapshot_version=snapshot,
        postcard=postcard,
        events=(
            MutationEvent(
                id=uuid.uuid4(),
                type=POSTCARD_READ_EVENT,
                occurred_at=utc_iso(row.read_at),
            ),
        ),
    )


async def _replay_read(
    session: AsyncSession,
    locked: postcard_repo.SpiritLock,
    claim: IdempotencyClaim,
    request_hash: str,
    postcard_id: UUID,
    now: datetime,
) -> PostcardReadSettlement:
    del now
    if claim.request_hash != request_hash:
        raise _api_error("IDEMPOTENCY_CONFLICT", status_code=409)
    if claim.status == "in_progress":
        raise _api_error("IDEMPOTENCY_IN_PROGRESS", status_code=409, retryable=True)
    resource_id = claim.resource_id if claim.resource_id is not None else postcard_id
    row = await postcard_repo.fetch_postcard_public(session, resource_id)
    if row is None or row.read_at is None:
        raise _api_error("NOT_FOUND", status_code=404)
    postcard = public_postcard_from_row(row)
    return PostcardReadSettlement(
        snapshot_version=locked.version,
        postcard=postcard,
        events=(
            MutationEvent(
                id=uuid.uuid4(),
                type=POSTCARD_READ_EVENT,
                occurred_at=utc_iso(row.read_at),
            ),
        ),
    )
