"""V4-T05 read clears unread only. Clear hides one side and keeps the rows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.domain.spirit_chat import ClearMark, ReadMark, clear_hash, unread_count, visible_seq
from app.repositories import spirit_chat as repository
from app.services.spirit_chat.command import ClaimState, ClearClaim, clear_thread, mark_thread_read

_ME = UUID("00000000-0000-4000-8000-0000000000e1")
_PEER_USER = UUID("00000000-0000-4000-8000-0000000000e2")
_MINE = UUID("00000000-0000-4000-8000-0000000000e3")
_PEER = UUID("00000000-0000-4000-8000-0000000000e4")
_THREAD = UUID("00000000-0000-4000-8000-0000000000e5")
_OTHER = UUID("00000000-0000-4000-8000-0000000000e6")
_CLIENT = UUID("00000000-0000-4000-8000-0000000000e7")


@dataclass
class _Side:
    user_id: UUID
    clear_seq: int
    last_read_seq: int
    last_visible_seq: int


class _Room:
    def __init__(self) -> None:
        self.messages = [(1, _PEER, "你好")]
        self.me = _Side(_ME, 0, 0, 1)
        self.peer = _Side(_PEER_USER, 0, 1, 1)
        self.outbox: list[tuple[UUID, str]] = []
        self.clears = 0

    def _side(self, user_id: UUID) -> _Side | None:
        if user_id == self.me.user_id:
            return self.me
        if user_id == self.peer.user_id:
            return self.peer
        return None

    def visible(self, side: _Side) -> list[int]:
        return [seq for seq, _, _ in self.messages if visible_seq(seq, side.clear_seq)]

    def unread(self, side: _Side, sender: UUID) -> int:
        seqs = tuple(seq for seq, who, _ in self.messages if who == sender)
        return unread_count(seqs, last_read_seq=side.last_read_seq, clear_seq=side.clear_seq)

    async def apply_clear(
        self,
        *,
        user_id: UUID,
        client_id: UUID,
        request_hash: str,
        thread_id: UUID,
    ) -> ClearMark | None:
        del client_id, request_hash
        side = self._side(user_id)
        if side is None or thread_id != _THREAD:
            return None
        highest = max((seq for seq, _, _ in self.messages), default=0)
        side.clear_seq = max(side.clear_seq, highest)
        self.clears += 1
        self.outbox.append((user_id, "thread.updated"))
        return ClearMark(thread_id, side.clear_seq)

    def receive(self, text: str) -> int:
        seq = max(item[0] for item in self.messages) + 1
        self.messages.append((seq, _PEER, text))
        self.me.last_visible_seq = seq
        self.peer.last_visible_seq = seq
        self.peer.last_read_seq = seq
        self.outbox.append((_ME, "message.created"))
        return seq


class _Claims:
    def __init__(self) -> None:
        self.rows: dict[UUID, tuple[str, ClaimState, object]] = {}

    async def claim(self, user_id: UUID, client_id: UUID, request_hash: str) -> ClearClaim:
        del user_id
        current = self.rows.get(client_id)
        if current is None:
            self.rows[client_id] = (request_hash, ClaimState.INSERTED, None)
            return ClearClaim(ClaimState.INSERTED)
        stored, state, mark = current
        if stored != request_hash:
            return ClearClaim(ClaimState.CONFLICT)
        if state is ClaimState.INSERTED:
            return ClearClaim(ClaimState.BUSY)
        return ClearClaim(ClaimState.REPLAY, mark if isinstance(mark, ClearMark) else None)

    async def release(self, user_id: UUID, client_id: UUID, request_hash: str) -> None:
        del user_id
        current = self.rows.get(client_id)
        if current and current[0] == request_hash and current[1] is ClaimState.INSERTED:
            del self.rows[client_id]


class _Bound:
    def __init__(self, room: _Room, claims: _Claims) -> None:
        self.room = room
        self.claims = claims

    async def apply_clear(
        self,
        *,
        user_id: UUID,
        client_id: UUID,
        request_hash: str,
        thread_id: UUID,
    ) -> ClearMark | None:
        mark = await self.room.apply_clear(
            user_id=user_id,
            client_id=client_id,
            request_hash=request_hash,
            thread_id=thread_id,
        )
        if mark is not None:
            self.claims.rows[client_id] = (request_hash, ClaimState.REPLAY, mark)
        return mark


def _read_mark(room: _Room, side: _Side) -> ReadMark:
    sender = _PEER if side.user_id == _ME else _MINE
    return ReadMark(_THREAD, side.last_read_seq, side.clear_seq, room.unread(side, sender))


class _Read:
    def __init__(self, room: _Room) -> None:
        self.room = room

    async def mark_read(self, user_id: UUID, thread_id: UUID):
        side = self.room._side(user_id)
        if side is None or thread_id != _THREAD:
            return None
        visible = [seq for seq, _, _ in self.room.messages if seq > side.clear_seq]
        target = max(visible) if visible else side.last_read_seq
        side.last_read_seq = max(side.last_read_seq, target)
        self.room.outbox.append((user_id, "thread.updated"))
        return _read_mark(self.room, side)


@pytest.mark.asyncio
async def test_read_keeps_text_and_zeroes_unread() -> None:
    room = _Room()
    first = await mark_thread_read(_Read(room), user_id=_ME, thread_id=_THREAD)
    assert first.unread == 0
    assert first.clear_seq == 0
    assert first.last_read_seq == 1
    assert room.messages == [(1, _PEER, "你好")]
    assert room.outbox == [(_ME, "thread.updated")]
    second = await mark_thread_read(_Read(room), user_id=_ME, thread_id=_THREAD)
    assert second.last_read_seq == 1
    assert room.me.clear_seq == 0


@pytest.mark.asyncio
async def test_clear_hides_only_my_side() -> None:
    room = _Room()
    claims = _Claims()
    before = list(room.messages)
    mark = await clear_thread(
        claims, _Bound(room, claims), user_id=_ME, client_id=_CLIENT, thread_id=_THREAD
    )
    assert mark.clear_seq == 1
    assert room.messages == before
    assert room.visible(room.me) == []
    assert room.visible(room.peer) == [1]
    assert room.peer.clear_seq == 0
    assert room.outbox == [(_ME, "thread.updated")]
    assert room.me.last_visible_seq <= room.me.clear_seq


@pytest.mark.asyncio
async def test_replay_does_not_hide_a_later_message() -> None:
    room = _Room()
    claims = _Claims()
    store = _Bound(room, claims)
    first = await clear_thread(claims, store, user_id=_ME, client_id=_CLIENT, thread_id=_THREAD)
    room.receive("又来了")
    second = await clear_thread(claims, store, user_id=_ME, client_id=_CLIENT, thread_id=_THREAD)
    assert second.clear_seq == first.clear_seq == 1
    assert room.clears == 1
    assert room.visible(room.me) == [2]
    assert room.me.last_visible_seq > room.me.clear_seq
    assert room.visible(room.peer) == [1, 2]


@pytest.mark.asyncio
async def test_conflict_busy_and_stranger_do_not_move_the_watermark() -> None:
    room = _Room()
    claims = _Claims()
    claims.rows[_CLIENT] = ("other", ClaimState.REPLAY, None)
    with pytest.raises(ApiError) as conflict:
        await clear_thread(
            claims, _Bound(room, claims), user_id=_ME, client_id=_CLIENT, thread_id=_THREAD
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    assert room.me.clear_seq == 0

    busy = _Claims()
    busy.rows[_CLIENT] = (clear_hash(_THREAD), ClaimState.INSERTED, None)
    with pytest.raises(ApiError) as held:
        await clear_thread(
            busy, _Bound(room, busy), user_id=_ME, client_id=_CLIENT, thread_id=_THREAD
        )
    assert held.value.code == "IDEMPOTENCY_IN_PROGRESS"
    assert room.clears == 0

    stranger = _Claims()
    with pytest.raises(ApiError) as missing:
        await clear_thread(
            stranger, _Bound(room, stranger), user_id=_OTHER, client_id=_CLIENT, thread_id=_THREAD
        )
    assert missing.value.code == "NOT_FOUND"
    assert room.messages == [(1, _PEER, "你好")]
    assert room.outbox == []
    assert _CLIENT not in stranger.rows

    with pytest.raises(ApiError) as unread:
        await mark_thread_read(_Read(room), user_id=_OTHER, thread_id=_THREAD)
    assert unread.value.code == "NOT_FOUND"


def test_repository_does_not_delete_messages_or_notify_the_peer() -> None:
    source = Path(repository.__file__).read_text(encoding="utf-8")
    command = Path(clear_thread.__code__.co_filename).read_text(encoding="utf-8")
    assert "DELETE FROM public.spirit_chat_messages" not in source
    assert "SET clear_seq = GREATEST" in source
    assert "SET last_read_seq = GREATEST" in source
    assert "FOR UPDATE" in source
    assert "complete_ask" not in command
    assert "chat(" not in command
    assert "v4_spirit_directory" not in command
