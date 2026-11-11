"""Read spirit chats. Does not write text, unread, or a thread."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.domain.cursor import (
    CURSOR_VERSION,
    InvalidCursor,
    MessageCursor,
    decode_spirit_chat_list_cursor,
    decode_spirit_chat_message_cursor,
    encode_spirit_chat_list_cursor,
    encode_spirit_chat_message_cursor,
    spirit_chat_list_filter_hash,
    spirit_chat_message_filter_hash,
    utc_iso,
)
from app.domain.spirit_chat import ChatMessageRow, OpenThreadRow, OwnFace, ThreadListRow
from app.domain.spirit_directory import DirectoryAnswer, DirectoryOutcome
from app.schemas.spirit_chat import (
    SpiritChatList,
    SpiritChatListItem,
    SpiritChatMessage,
    SpiritChatMessagePage,
    SpiritChatWindow,
)


class ChatReads(Protocol):
    async def visible_threads(
        self,
        user_id: UUID,
        *,
        snapshot: datetime,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ThreadListRow]: ...

    async def unread_total(self, user_id: UUID) -> int: ...

    async def open_thread(self, user_id: UUID, spirit_id: UUID) -> OpenThreadRow | None: ...

    async def own_face(self, user_id: UUID) -> OwnFace | None: ...

    async def visible_messages(
        self,
        user_id: UUID,
        thread_id: UUID,
        *,
        snapshot: datetime,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[ChatMessageRow] | None: ...


class PeerCards(Protocol):
    async def lookup(self, viewer_id: UUID, spirit_id: UUID) -> DirectoryAnswer: ...


async def list_chats(
    store: ChatReads,
    *,
    user_id: UUID,
    cursor: str | None,
    limit: int,
    secret: bytes,
    now: datetime,
) -> SpiritChatList:
    snapshot, before = _list_position(cursor, user_id=user_id, secret=secret, now=now)
    rows = await store.visible_threads(user_id, snapshot=snapshot, before=before, limit=limit + 1)
    page, has_more, next_cursor = _page_threads(
        rows, limit=limit, snapshot=snapshot, secret=secret, user_id=user_id
    )
    return SpiritChatList(
        items=[_list_item(row) for row in page],
        unread_total=await store.unread_total(user_id),
        has_more=has_more,
        next_cursor=next_cursor,
    )


async def list_chat_messages(
    store: ChatReads,
    *,
    user_id: UUID,
    thread_id: UUID,
    cursor: str | None,
    limit: int,
    secret: bytes,
    now: datetime,
) -> SpiritChatMessagePage:
    snapshot, before = _message_position(
        cursor, user_id=user_id, thread_id=thread_id, secret=secret, now=now
    )
    rows = await store.visible_messages(
        user_id, thread_id, snapshot=snapshot, before=before, limit=limit + 1
    )
    if rows is None:
        raise _missing()
    page, has_more, next_cursor = _page_messages(
        rows,
        limit=limit,
        snapshot=snapshot,
        secret=secret,
        user_id=user_id,
        thread_id=thread_id,
    )
    return SpiritChatMessagePage(
        items=[_message_item(row) for row in page],
        has_more=has_more,
        next_cursor=next_cursor,
    )


async def open_by_spirit(
    store: ChatReads,
    cards: PeerCards,
    *,
    user_id: UUID,
    spirit_id: UUID,
    cursor: str | None,
    limit: int,
    secret: bytes,
    now: datetime,
) -> SpiritChatWindow:
    found = await store.open_thread(user_id, spirit_id)
    if found is None:
        return await _empty_window(store, cards, user_id=user_id, spirit_id=spirit_id)
    snapshot, before = _message_position(
        cursor, user_id=user_id, thread_id=found.thread_id, secret=secret, now=now
    )
    rows = await store.visible_messages(
        user_id, found.thread_id, snapshot=snapshot, before=before, limit=limit + 1
    )
    if rows is None:
        raise _missing()
    page, has_more, next_cursor = _page_messages(
        rows,
        limit=limit,
        snapshot=snapshot,
        secret=secret,
        user_id=user_id,
        thread_id=found.thread_id,
    )
    own_name, own_avatar = await _own_face(store, user_id, found)
    return SpiritChatWindow(
        thread_id=found.thread_id,
        spirit_id=found.spirit_id,
        display_name=found.display_name,
        avatar_url=found.avatar_url,
        peer_available=found.available,
        own_display_name=own_name,
        own_avatar_url=own_avatar,
        messages=[_message_item(row) for row in page],
        has_more=has_more,
        next_cursor=next_cursor,
    )


async def _empty_window(
    store: ChatReads,
    cards: PeerCards,
    *,
    user_id: UUID,
    spirit_id: UUID,
) -> SpiritChatWindow:
    face = await store.own_face(user_id)
    if face is None:
        raise _missing()
    answer = await cards.lookup(user_id, spirit_id)
    if answer.outcome is not DirectoryOutcome.AVAILABLE or answer.card is None:
        raise _missing()
    card = answer.card
    return SpiritChatWindow(
        thread_id=None,
        spirit_id=card.spirit_id,
        display_name=card.display_name,
        avatar_url=card.avatar_url,
        peer_available=True,
        own_display_name=face.display_name,
        own_avatar_url=face.avatar_url,
        messages=[],
        has_more=False,
        next_cursor=None,
    )


async def _own_face(store: ChatReads, user_id: UUID, found: OpenThreadRow) -> tuple[str, str]:
    face = await store.own_face(user_id)
    name = face.display_name if face is not None else found.own_display_name
    avatar = face.avatar_url if face is not None else found.own_avatar_url
    if not name or not avatar:
        raise _missing()
    return name, avatar


def _list_position(
    cursor: str | None,
    *,
    user_id: UUID,
    secret: bytes,
    now: datetime,
) -> tuple[datetime, tuple[datetime, UUID] | None]:
    if cursor is None:
        return now, None
    decoded = _decode(
        decode_spirit_chat_list_cursor,
        cursor,
        secret=secret,
        expected=spirit_chat_list_filter_hash(user_id),
    )
    return decoded.snapshot_at, (decoded.sort_time, decoded.id)


def _message_position(
    cursor: str | None,
    *,
    user_id: UUID,
    thread_id: UUID,
    secret: bytes,
    now: datetime,
) -> tuple[datetime, tuple[datetime, UUID] | None]:
    if cursor is None:
        return now, None
    decoded = _decode(
        decode_spirit_chat_message_cursor,
        cursor,
        secret=secret,
        expected=spirit_chat_message_filter_hash(user_id, thread_id),
    )
    return decoded.snapshot_at, (decoded.sort_time, decoded.id)


def _decode(decoder, token: str, *, secret: bytes, expected: str) -> MessageCursor:
    try:
        return decoder(token, secret=secret, expected_filter_hash=expected)
    except InvalidCursor as exc:
        raise _invalid_cursor() from exc


def _page_threads(
    rows: list[ThreadListRow],
    *,
    limit: int,
    snapshot: datetime,
    secret: bytes,
    user_id: UUID,
) -> tuple[list[ThreadListRow], bool, str | None]:
    has_more = len(rows) > limit
    page = rows[:limit]
    if not has_more or not page:
        return page, False, None
    last = page[-1]
    token = encode_spirit_chat_list_cursor(
        MessageCursor(
            version=CURSOR_VERSION,
            sort_time=last.created_at,
            id=last.thread_id,
            snapshot_at=snapshot,
            filter_hash=spirit_chat_list_filter_hash(user_id),
        ),
        secret=secret,
    )
    return page, True, token


def _page_messages(
    rows: list[ChatMessageRow],
    *,
    limit: int,
    snapshot: datetime,
    secret: bytes,
    user_id: UUID,
    thread_id: UUID,
) -> tuple[list[ChatMessageRow], bool, str | None]:
    has_more = len(rows) > limit
    page = rows[:limit]
    if not has_more or not page:
        return page, False, None
    last = page[-1]
    token = encode_spirit_chat_message_cursor(
        MessageCursor(
            version=CURSOR_VERSION,
            sort_time=last.created_at,
            id=last.id,
            snapshot_at=snapshot,
            filter_hash=spirit_chat_message_filter_hash(user_id, thread_id),
        ),
        secret=secret,
    )
    return page, True, token


def _list_item(row: ThreadListRow) -> SpiritChatListItem:
    return SpiritChatListItem(
        thread_id=row.thread_id,
        spirit_id=row.spirit_id,
        display_name=row.display_name,
        avatar_url=row.avatar_url,
        peer_available=row.available,
        text=row.text,
        created_at=utc_iso(row.created_at),
        unread=row.unread,
    )


def _message_item(row: ChatMessageRow) -> SpiritChatMessage:
    return SpiritChatMessage(
        id=row.id,
        seq=row.seq,
        mine=row.mine,
        text=row.text,
        created_at=utc_iso(row.created_at),
    )


def delivery_visible(*, clear_seq: int | None, seq: int, hidden: bool) -> bool:
    if clear_seq is None:
        return False
    if hidden:
        return True
    return seq > clear_seq


def _missing() -> ApiError:
    return ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)


def _invalid_cursor() -> ApiError:
    return ApiError("INVALID_CURSOR", public_error_message("INVALID_CURSOR"), status_code=422)
