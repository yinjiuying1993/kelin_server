"""V4-T04 send claims idempotency before directory or safety, and writes once."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.errors import ApiError
from app.domain.spirit_chat import send_hash
from app.domain.spirit_directory import DirectoryAnswer, DirectoryOutcome, SpiritDirectoryCard
from app.providers.errors import ProviderError
from app.services.spirit_chat import command as command_module
from app.services.spirit_chat.command import ClaimResult, ClaimState, SentMessage, send_message

_USER = UUID("00000000-0000-4000-8000-0000000000d1")
_OWN = UUID("00000000-0000-4000-8000-0000000000d2")
_PEER = UUID("00000000-0000-4000-8000-0000000000d3")
_PEER_OWNER = UUID("00000000-0000-4000-8000-0000000000d4")
_NOW = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)


def _card(spirit_id: UUID, *, available: bool = True) -> SpiritDirectoryCard:
    return SpiritDirectoryCard(
        spirit_id=spirit_id,
        owner_user_id=_USER if spirit_id == _OWN else _PEER_OWNER,
        display_name="原名" if spirit_id == _OWN else "对方",
        avatar_url=f"https://kelin.invalid/{spirit_id}",
        available=available,
    )


class _Claims:
    def __init__(self) -> None:
        self.rows: dict[UUID, tuple[str, ClaimState, SentMessage | None]] = {}
        self.released: list[UUID] = []

    async def claim(self, user_id: UUID, client_id: UUID, request_hash: str) -> ClaimResult:
        del user_id
        current = self.rows.get(client_id)
        if current is None:
            self.rows[client_id] = (request_hash, ClaimState.INSERTED, None)
            return ClaimResult(ClaimState.INSERTED)
        digest, state, sent = current
        if digest != request_hash:
            return ClaimResult(ClaimState.CONFLICT)
        if state is ClaimState.INSERTED:
            return ClaimResult(ClaimState.BUSY)
        return ClaimResult(ClaimState.REPLAY, sent)

    async def release(self, user_id: UUID, client_id: UUID, request_hash: str) -> None:
        del user_id, request_hash
        self.rows.pop(client_id, None)
        self.released.append(client_id)


class _Cards:
    def __init__(self, card: SpiritDirectoryCard | None) -> None:
        self.card = card
        self.calls = 0

    async def own(self, user_id: UUID) -> SpiritDirectoryCard | None:
        del user_id
        self.calls += 1
        return self.card


class _Directory:
    def __init__(self, outcome: DirectoryOutcome, card: SpiritDirectoryCard | None = None) -> None:
        self.answer = DirectoryAnswer(outcome, card)
        self.calls = 0

    async def lookup(self, viewer_id: UUID, spirit_id: UUID) -> DirectoryAnswer:
        del viewer_id, spirit_id
        self.calls += 1
        return self.answer


class _Threads:
    def __init__(self, thread_id: UUID | None = None) -> None:
        self.found = thread_id

    async def thread_id(self, low_id: UUID, high_id: UUID) -> UUID | None:
        del low_id, high_id
        return self.found


class _Safety:
    def __init__(self, decision: str | Exception = "allow") -> None:
        self.decision_value = decision
        self.calls = 0

    async def decision(self, text: str) -> str:
        del text
        self.calls += 1
        if isinstance(self.decision_value, Exception):
            raise self.decision_value
        return self.decision_value


class _Writer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[SentMessage] = []
        self.outbox: list[tuple[UUID, str]] = []
        self.threads: dict[tuple[UUID, UUID], UUID] = {}
        self._lock = asyncio.Lock()

    async def commit_send(
        self,
        *,
        user_id: UUID,
        client_id: UUID,
        request_hash: str,
        own: SpiritDirectoryCard,
        peer: SpiritDirectoryCard,
        text: str,
        created_at: datetime,
    ) -> SentMessage:
        del user_id, request_hash
        if self.fail:
            raise RuntimeError("commit failed")
        async with self._lock:
            pair = tuple(sorted((own.spirit_id, peer.spirit_id)))
            thread_id = self.threads.setdefault(pair, uuid4())
            seq = 1 + sum(1 for item in self.messages if item.thread_id == thread_id)
            sent = SentMessage(
                thread_id=thread_id,
                message_id=uuid4(),
                seq=seq,
                text=text,
                peer_spirit_id=peer.spirit_id,
                peer_display_name=peer.display_name,
                peer_avatar_url=peer.avatar_url,
                peer_available=peer.available,
                created_at=created_at,
            )
            self.messages.append(sent)
            self.outbox.append((sent.message_id, "message.created"))
            self.outbox.append((sent.message_id, "thread.updated"))
        return sent


def _bind(writer: _Writer, claims: _Claims) -> _Writer:
    original = writer.commit_send

    async def _commit(**kwargs: object) -> SentMessage:
        sent = await original(**kwargs)
        client_id = kwargs["client_id"]
        digest = kwargs["request_hash"]
        assert isinstance(client_id, UUID)
        assert isinstance(digest, str)
        claims.rows[client_id] = (digest, ClaimState.REPLAY, sent)
        return sent

    writer.commit_send = _commit  # type: ignore[method-assign]
    return writer


async def _send(
    claims: _Claims,
    cards: _Cards,
    directory: _Directory,
    threads: _Threads,
    safety: _Safety,
    writer: _Writer,
    *,
    client_id: UUID,
    text: str,
    peer: UUID = _PEER,
) -> SentMessage:
    return await send_message(
        claims,
        cards,
        directory,
        threads,
        safety,
        writer,
        user_id=_USER,
        client_id=client_id,
        peer_spirit_id=peer,
        text=text,
        now=_NOW,
    )


@pytest.mark.asyncio
async def test_blank_and_overlong_text_write_nothing() -> None:
    claims = _Claims()
    writer = _Writer()
    for text in ("   ", "x" * 501):
        with pytest.raises(ApiError) as caught:
            await _send(
                claims,
                _Cards(_card(_OWN)),
                _Directory(DirectoryOutcome.AVAILABLE),
                _Threads(),
                _Safety(),
                writer,
                client_id=uuid4(),
                text=text,
            )
        assert caught.value.code == "INVALID_INPUT"
    assert claims.rows == {}
    assert writer.messages == []
    assert writer.outbox == []


@pytest.mark.asyncio
async def test_same_client_replays_and_a_changed_body_conflicts() -> None:
    claims = _Claims()
    cards = _Cards(_card(_OWN))
    directory = _Directory(DirectoryOutcome.AVAILABLE, _card(_PEER))
    writer = _bind(_Writer(), claims)
    client_id = uuid4()
    first = await _send(
        claims, cards, directory, _Threads(), _Safety(), writer, client_id=client_id, text="你好"
    )
    second = await _send(
        claims, cards, directory, _Threads(), _Safety(), writer, client_id=client_id, text="你好"
    )
    assert second.message_id == first.message_id
    assert directory.calls == 1
    assert len(writer.messages) == 1
    with pytest.raises(ApiError) as conflict:
        await _send(
            claims,
            cards,
            directory,
            _Threads(),
            _Safety(),
            writer,
            client_id=client_id,
            text="换一句",
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    assert len(writer.messages) == 1


@pytest.mark.asyncio
async def test_in_progress_claim_does_not_call_directory() -> None:
    claims = _Claims()
    client_id = uuid4()
    claims.rows[client_id] = (send_hash(_PEER, "你好"), ClaimState.INSERTED, None)
    directory = _Directory(DirectoryOutcome.AVAILABLE, _card(_PEER))
    with pytest.raises(ApiError) as caught:
        await _send(
            claims,
            _Cards(_card(_OWN)),
            directory,
            _Threads(),
            _Safety(),
            _Writer(),
            client_id=client_id,
            text="你好",
        )
    assert caught.value.code == "IDEMPOTENCY_IN_PROGRESS"
    assert caught.value.retryable is True
    assert directory.calls == 0


@pytest.mark.asyncio
async def test_missing_and_unavailable_without_a_thread_are_not_found() -> None:
    claims = _Claims()
    directory = _Directory(DirectoryOutcome.UNAVAILABLE, _card(_PEER, available=False))
    safety = _Safety()
    with pytest.raises(ApiError) as missing:
        await _send(
            claims,
            _Cards(None),
            directory,
            _Threads(),
            safety,
            _Writer(),
            client_id=uuid4(),
            text="你好",
        )
    assert missing.value.code == "NOT_FOUND"
    with pytest.raises(ApiError) as own:
        await _send(
            claims,
            _Cards(_card(_OWN)),
            directory,
            _Threads(),
            safety,
            _Writer(),
            client_id=uuid4(),
            text="你好",
            peer=_OWN,
        )
    assert own.value.code == "NOT_FOUND"
    with pytest.raises(ApiError) as hidden:
        await _send(
            claims,
            _Cards(_card(_OWN)),
            directory,
            _Threads(),
            safety,
            _Writer(),
            client_id=uuid4(),
            text="你好",
        )
    assert hidden.value.code == "NOT_FOUND"
    assert safety.calls == 0


@pytest.mark.asyncio
async def test_existing_thread_and_unavailable_peer_rejects_the_send() -> None:
    claims = _Claims()
    safety = _Safety()
    writer = _Writer()
    directory = _Directory(DirectoryOutcome.MISSING, None)
    with pytest.raises(ApiError) as caught:
        await _send(
            claims,
            _Cards(_card(_OWN)),
            directory,
            _Threads(uuid4()),
            safety,
            writer,
            client_id=uuid4(),
            text="你好",
        )
    assert caught.value.code == "SPIRIT_UNAVAILABLE"
    assert writer.messages == []
    assert writer.outbox == []
    assert safety.calls == 0
    assert claims.rows == {}


@pytest.mark.asyncio
async def test_rejected_or_failed_safety_and_failed_commit_leave_no_message() -> None:
    claims = _Claims()
    cards = _Cards(_card(_OWN))
    directory = _Directory(DirectoryOutcome.AVAILABLE, _card(_PEER))
    writer = _Writer()
    with pytest.raises(ApiError) as blocked:
        await _send(
            claims,
            cards,
            directory,
            _Threads(),
            _Safety("block"),
            writer,
            client_id=uuid4(),
            text="你好",
        )
    assert blocked.value.code == "MODERATION_REJECTED"
    with pytest.raises(ApiError) as timed:
        await _send(
            claims,
            cards,
            directory,
            _Threads(),
            _Safety(ProviderError("PROVIDER_TIMEOUT")),
            writer,
            client_id=uuid4(),
            text="你好",
        )
    assert timed.value.code == "PROVIDER_TIMEOUT"
    failing = _Writer(fail=True)
    with pytest.raises(RuntimeError):
        await _send(
            claims,
            cards,
            directory,
            _Threads(),
            _Safety(),
            failing,
            client_id=uuid4(),
            text="你好",
        )
    assert writer.messages == []
    assert writer.outbox == []
    assert failing.messages == []
    assert failing.outbox == []


@pytest.mark.asyncio
async def test_two_first_sends_keep_one_thread_and_two_sequences() -> None:
    claims = _Claims()
    writer = _bind(_Writer(), claims)
    cards = _Cards(_card(_OWN))
    directory = _Directory(DirectoryOutcome.AVAILABLE, _card(_PEER))
    first, second = await asyncio.gather(
        _send(
            claims, cards, directory, _Threads(), _Safety(), writer, client_id=uuid4(), text="一"
        ),
        _send(
            claims, cards, directory, _Threads(), _Safety(), writer, client_id=uuid4(), text="二"
        ),
    )
    assert first.thread_id == second.thread_id
    assert {first.seq, second.seq} == {1, 2}
    assert [item[1] for item in writer.outbox].count("message.created") == 2


def test_send_does_not_call_a_reply_model() -> None:
    source = inspect.getsource(command_module)
    for forbidden in ("complete_ask", "complete_match", "complete_official", "chat("):
        assert forbidden not in source
    fixed = Path(__file__).resolve().parents[2] / "app" / "db" / "migrations" / "versions"
    returning = (fixed / "20261110_0137_spirit_chat_send_returning.py").read_text(encoding="utf-8")
    columns = (fixed / "20261110_0138_spirit_chat_send_columns.py").read_text(encoding="utf-8")
    assert "spirit_chat_messages.created_at" in returning
    assert "#variable_conflict use_column" in columns
