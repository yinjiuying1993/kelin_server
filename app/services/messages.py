"""Load owner message pages. Spec §§10.2, 15.1."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.cursor import (
    CURSOR_VERSION,
    InvalidCursor,
    MessageCursor,
    decode_message_cursor,
    encode_message_cursor,
    messages_filter_hash,
    utc_iso,
)
from app.domain.memory_recall import memory_ids_of, visible_source_refs
from app.repositories import memory_recall as recall_repo
from app.repositories import messages as messages_repo
from app.schemas.jsonb import SourceRef, parse_source_refs
from app.schemas.messages import (
    MessagePage,
    MessagePublic,
    MessageRole,
    MessageSource,
    MessageStatus,
)


def _invalid_cursor() -> ApiError:
    return ApiError(
        "INVALID_CURSOR",
        public_error_message("INVALID_CURSOR"),
        status_code=422,
        retryable=False,
    )


async def load_message_page(
    session: AsyncSession,
    user: CurrentUser,
    *,
    cursor: str | None,
    limit: int,
    secret: bytes,
) -> MessagePage:
    filter_hash = messages_filter_hash(user.id)
    cursor_time = None
    cursor_id = None
    if cursor is None:
        snapshot_at = await messages_repo.fetch_transaction_now(session)
    else:
        try:
            decoded = decode_message_cursor(
                cursor,
                secret=secret,
                expected_filter_hash=filter_hash,
            )
        except InvalidCursor as exc:
            raise _invalid_cursor() from exc
        snapshot_at = decoded.snapshot_at
        cursor_time = decoded.sort_time
        cursor_id = decoded.id
    spirit_id = await messages_repo.fetch_owned_spirit_id(session, user.id)
    if spirit_id is None:
        return MessagePage(
            items=[],
            next_cursor=None,
            has_more=False,
            snapshot_at=utc_iso(snapshot_at),
        )
    rows = await messages_repo.list_messages_keyset(
        session,
        spirit_id=spirit_id,
        snapshot_at=snapshot_at,
        fetch_limit=limit + 1,
        cursor_time=cursor_time,
        cursor_id=cursor_id,
    )
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    parsed_refs = [tuple(parse_source_refs(row.source_refs)) for row in page_rows]
    wanted = tuple(memory_id for refs in parsed_refs for memory_id in memory_ids_of(refs))
    active_ids = await recall_repo.fetch_active_owned_memory_ids(
        session, owner_id=user.id, spirit_id=spirit_id, memory_ids=wanted
    )
    items = [
        _public_message(row, source_refs=visible_source_refs(refs, active_memory_ids=active_ids))
        for row, refs in zip(page_rows, parsed_refs, strict=True)
    ]
    next_cursor = None
    if has_more:
        last = page_rows[-1]
        next_cursor = encode_message_cursor(
            MessageCursor(
                version=CURSOR_VERSION,
                sort_time=last.created_at,
                id=last.id,
                snapshot_at=snapshot_at,
                filter_hash=filter_hash,
            ),
            secret=secret,
        )
    return MessagePage(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
        snapshot_at=utc_iso(snapshot_at),
    )


def _public_message(
    row: messages_repo.MessageListRow,
    *,
    source_refs: tuple[SourceRef, ...],
) -> MessagePublic:
    if row.role not in ("user", "spirit"):
        raise RuntimeError("system message must not be listed")
    role: MessageRole = "user" if row.role == "user" else "spirit"
    return MessagePublic(
        id=row.id,
        client_id=row.client_id,
        role=role,
        content=row.content,
        source=_message_source(row.source),
        status=_message_status(row.status),
        reply_to_message_id=row.reply_to_message_id,
        source_refs=list(source_refs),
        created_at=utc_iso(row.created_at),
    )


def _message_source(value: str) -> MessageSource:
    if value == "text":
        return "text"
    if value == "voice":
        return "voice"
    if value == "onboarding":
        return "onboarding"
    raise RuntimeError("invalid message source")


def _message_status(value: str) -> MessageStatus:
    if value == "accepted":
        return "accepted"
    if value == "generated":
        return "generated"
    if value == "failed":
        return "failed"
    raise RuntimeError("invalid message status")
