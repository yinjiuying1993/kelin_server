"""V4-T06 reads stay on the caller's watermarks and do not write."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.domain.cursor import (
    MessageCursor,
    encode_spirit_chat_list_cursor,
    spirit_chat_list_filter_hash,
)
from app.domain.spirit_chat import (
    ChatMessageRow,
    OpenThreadRow,
    OwnFace,
    ThreadListRow,
    unread_count,
    visible_seq,
)
from app.domain.spirit_directory import DirectoryAnswer, DirectoryOutcome, SpiritDirectoryCard
from app.repositories.spirit_chat import LIST_THREADS_SQL, OPEN_THREAD_SQL, VISIBLE_MESSAGES_SQL
from app.schemas.spirit_chat import SpiritChatList, SpiritChatMessagePage, SpiritChatWindow
from app.services.spirit_chat import query as query_module
from app.services.spirit_chat.query import list_chat_messages, list_chats, open_by_spirit

_USER = UUID("00000000-0000-4000-8000-0000000000c1")
_OTHER = UUID("00000000-0000-4000-8000-0000000000c2")
_OWN = UUID("00000000-0000-4000-8000-0000000000c3")
_PEER = UUID("00000000-0000-4000-8000-0000000000c4")
_OLDER = UUID("00000000-0000-4000-8000-0000000000c5")
_NEWER = UUID("00000000-0000-4000-8000-0000000000c6")
_HIDDEN = UUID("00000000-0000-4000-8000-0000000000c7")
_SHOWN = UUID("00000000-0000-4000-8000-0000000000c8")
_SECRET = b"spirit-chat-test"
_NOW = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
_EARLY = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)
_LATE = datetime(2026, 11, 10, 4, 0, tzinfo=UTC)


@dataclass
class _Msg:
    id: UUID
    seq: int
    sender: UUID
    text: str
    created_at: datetime


@dataclass
class _Thread:
    thread_id: UUID
    spirit_id: UUID
    display_name: str
    avatar_url: str
    available: bool
    clear_seq: int
    last_read_seq: int
    last_visible_seq: int
    own_display_name: str
    own_avatar_url: str
    messages: list[_Msg] = field(default_factory=list)

    def unread(self) -> int:
        seqs = tuple(item.seq for item in self.messages if item.sender != _OWN)
        return unread_count(seqs, last_read_seq=self.last_read_seq, clear_seq=self.clear_seq)


def _message(seq: int, sender: UUID, text: str, at: datetime) -> _Msg:
    return _Msg(UUID(int=seq), seq, sender, text, at)


class _Store:
    def __init__(self, threads: list[_Thread], face: OwnFace | None) -> None:
        self.threads = threads
        self.face = face
        self.face_calls = 0

    def _mine(self, thread_id: UUID) -> _Thread | None:
        for item in self.threads:
            if item.thread_id == thread_id:
                return item
        return None

    async def visible_threads(self, user_id, *, snapshot, before, limit):
        del user_id
        rows = []
        for item in self.threads:
            if item.last_visible_seq <= item.clear_seq:
                continue
            visible = [msg for msg in item.messages if msg.seq == item.last_visible_seq]
            if not visible or visible[0].created_at > snapshot:
                continue
            text = visible[0]
            row = ThreadListRow(
                item.thread_id,
                item.spirit_id,
                item.display_name,
                item.avatar_url,
                item.available,
                text.text,
                text.created_at,
                item.unread(),
            )
            if before is not None and (row.created_at, row.thread_id) >= before:
                continue
            rows.append(row)
        rows.sort(key=lambda row: (row.created_at, row.thread_id), reverse=True)
        return rows[:limit]

    async def unread_total(self, user_id) -> int:
        del user_id
        return sum(item.unread() for item in self.threads if item.last_visible_seq > item.clear_seq)

    async def open_thread(self, user_id, spirit_id):
        del user_id
        for item in self.threads:
            if item.spirit_id == spirit_id:
                return OpenThreadRow(
                    item.thread_id,
                    item.spirit_id,
                    item.display_name,
                    item.avatar_url,
                    item.available,
                    item.own_display_name,
                    item.own_avatar_url,
                )
        return None

    async def own_face(self, user_id):
        del user_id
        self.face_calls += 1
        return self.face

    async def visible_messages(self, user_id, thread_id, *, snapshot, before, limit):
        del user_id
        item = self._mine(thread_id)
        if item is None:
            return None
        rows = []
        for msg in item.messages:
            if not visible_seq(msg.seq, item.clear_seq) or msg.created_at > snapshot:
                continue
            if before is not None and (msg.created_at, msg.id) >= before:
                continue
            rows.append(
                ChatMessageRow(msg.id, msg.seq, msg.sender == _OWN, msg.text, msg.created_at)
            )
        rows.sort(key=lambda row: (row.created_at, row.id), reverse=True)
        return rows[:limit]


class _Cards:
    def __init__(self, outcome: DirectoryOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    async def lookup(self, viewer_id, spirit_id):
        del viewer_id
        self.calls += 1
        if self.outcome is not DirectoryOutcome.AVAILABLE:
            return DirectoryAnswer(self.outcome, None)
        card = SpiritDirectoryCard(_PEER, _OTHER, "目录名", "https://kelin.invalid/peer", True)
        if spirit_id != _PEER:
            card = SpiritDirectoryCard(
                spirit_id, _OTHER, "目录名", "https://kelin.invalid/peer", True
            )
        return DirectoryAnswer(self.outcome, card)


def _face() -> OwnFace:
    return OwnFace(_OWN, "当场", "https://kelin.invalid/own")


def _sample() -> list[_Thread]:
    older = _Thread(
        _OLDER, _PEER, "旧副本", "https://kelin.invalid/old", True, 0, 0, 1, "副本", "https://kelin.invalid/copy"
    )
    older.messages = [_message(1, _PEER, "旧", _EARLY)]
    newer = _Thread(
        _NEWER,
        _SHOWN,
        "新副本",
        "https://kelin.invalid/new",
        True,
        1,
        1,
        3,
        "副本",
        "https://kelin.invalid/copy",
    )
    newer.messages = [
        _message(1, _PEER, "藏", _EARLY),
        _message(2, _OWN, "我", datetime(2026, 11, 10, 3, 30, tzinfo=UTC)),
        _message(3, _PEER, "新", _LATE),
    ]
    hidden = _Thread(
        _HIDDEN, _PEER, "藏副本", "https://kelin.invalid/hide", True, 1, 0, 1, "副本", "https://kelin.invalid/copy"
    )
    hidden.messages = [_message(1, _PEER, "不见", _LATE)]
    return [older, newer, hidden]


@pytest.mark.asyncio
async def test_list_is_visible_newest_first_and_empty_is_success() -> None:
    empty = await list_chats(
        _Store([], _face()), user_id=_USER, cursor=None, limit=30, secret=_SECRET, now=_NOW
    )
    assert empty.items == []
    assert empty.unread_total == 0
    assert empty.has_more is False
    store = _Store(_sample(), _face())
    before = [item.last_read_seq for item in store.threads]
    first = await list_chats(store, user_id=_USER, cursor=None, limit=1, secret=_SECRET, now=_NOW)
    assert [item.thread_id for item in first.items] == [_NEWER]
    assert first.items[0].text == "新"
    assert first.items[0].unread == 1
    assert first.unread_total == 2
    assert first.has_more is True
    assert first.next_cursor
    second = await list_chats(
        store, user_id=_USER, cursor=first.next_cursor, limit=1, secret=_SECRET, now=_NOW
    )
    assert [item.thread_id for item in second.items] == [_OLDER]
    assert [item.last_read_seq for item in store.threads] == before
    assert _HIDDEN not in {item.thread_id for item in first.items + second.items}


@pytest.mark.asyncio
async def test_messages_skip_cleared_seq_and_reject_a_foreign_cursor() -> None:
    store = _Store(_sample(), _face())
    first = await list_chat_messages(
        store, user_id=_USER, thread_id=_NEWER, cursor=None, limit=1, secret=_SECRET, now=_NOW
    )
    assert [item.seq for item in first.items] == [3]
    second = await list_chat_messages(
        store,
        user_id=_USER,
        thread_id=_NEWER,
        cursor=first.next_cursor,
        limit=1,
        secret=_SECRET,
        now=_NOW,
    )
    assert [item.seq for item in second.items] == [2]
    with pytest.raises(ApiError) as missing:
        await list_chat_messages(
            store, user_id=_USER, thread_id=_OTHER, cursor=None, limit=30, secret=_SECRET, now=_NOW
        )
    assert missing.value.code == "NOT_FOUND"
    foreign = encode_spirit_chat_list_cursor(
        MessageCursor(1, _LATE, _NEWER, _NOW, spirit_chat_list_filter_hash(_OTHER)),
        secret=_SECRET,
    )
    with pytest.raises(ApiError) as bad:
        await list_chats(store, user_id=_USER, cursor=foreign, limit=30, secret=_SECRET, now=_NOW)
    assert bad.value.code == "INVALID_CURSOR"


@pytest.mark.asyncio
async def test_open_uses_directory_only_when_the_thread_is_absent() -> None:
    cards = _Cards(DirectoryOutcome.AVAILABLE)
    store = _Store(_sample(), _face())
    window = await open_by_spirit(
        store,
        cards,
        user_id=_USER,
        spirit_id=_SHOWN,
        cursor=None,
        limit=10,
        secret=_SECRET,
        now=_NOW,
    )
    assert cards.calls == 0
    assert window.thread_id == _NEWER
    assert window.display_name == "新副本"
    assert window.own_display_name == "当场"
    assert [item.seq for item in window.messages] == [3, 2]
    missed = _Cards(DirectoryOutcome.UNAVAILABLE)
    with pytest.raises(ApiError) as absent:
        await open_by_spirit(
            _Store([], _face()),
            missed,
            user_id=_USER,
            spirit_id=_PEER,
            cursor=None,
            limit=10,
            secret=_SECRET,
            now=_NOW,
        )
    assert absent.value.code == "NOT_FOUND"
    assert missed.calls == 1
    opened = _Cards(DirectoryOutcome.AVAILABLE)
    fresh = _Store([], _face())
    empty = await open_by_spirit(
        fresh,
        opened,
        user_id=_USER,
        spirit_id=_PEER,
        cursor=None,
        limit=10,
        secret=_SECRET,
        now=_NOW,
    )
    assert opened.calls == 1
    assert empty.thread_id is None
    assert empty.peer_available is True
    assert empty.messages == []
    assert fresh.threads == []
    stranger = _Cards(DirectoryOutcome.AVAILABLE)
    with pytest.raises(ApiError) as no_face:
        await open_by_spirit(
            _Store([], None),
            stranger,
            user_id=_USER,
            spirit_id=_PEER,
            cursor=None,
            limit=10,
            secret=_SECRET,
            now=_NOW,
        )
    assert no_face.value.code == "NOT_FOUND"
    assert stranger.calls == 0


@pytest.mark.asyncio
async def test_open_keeps_the_saved_face_when_the_live_face_is_missing() -> None:
    store = _Store(_sample(), None)
    window = await open_by_spirit(
        store,
        _Cards(DirectoryOutcome.AVAILABLE),
        user_id=_USER,
        spirit_id=_SHOWN,
        cursor=None,
        limit=10,
        secret=_SECRET,
        now=_NOW,
    )
    assert window.own_display_name == "副本"
    assert window.own_avatar_url == "https://kelin.invalid/copy"


def test_reads_do_not_write_or_put_a_user_id_on_the_response() -> None:
    source = Path(query_module.__file__).read_text(encoding="utf-8")
    assert "UPDATE" not in source
    assert "INSERT" not in source
    assert "DELETE" not in source
    assert "ask_questions" not in source
    assert "room_messages" not in source
    assert "directory" not in inspect.signature(list_chats).parameters
    assert "directory" not in inspect.signature(list_chat_messages).parameters
    for statement in (LIST_THREADS_SQL, VISIBLE_MESSAGES_SQL, OPEN_THREAD_SQL):
        assert "UPDATE" not in statement
        assert "v4_spirit_directory" not in statement
        assert "ask_questions" not in statement
    assert "last_visible_seq > p.clear_seq" in LIST_THREADS_SQL
    assert "m.seq > p.clear_seq" in VISIBLE_MESSAGES_SQL
    for model in (SpiritChatList, SpiritChatWindow, SpiritChatMessagePage):
        assert "user_id" not in model.model_fields
