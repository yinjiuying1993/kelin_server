"""Send one human text. Directory and safety stay outside the write."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.domain.spirit_chat import (
    ChatTextRejected,
    ClearMark,
    ReadMark,
    clear_hash,
    message_text,
    ordered_spirit_pair,
    send_hash,
)
from app.domain.spirit_directory import DirectoryAnswer, DirectoryOutcome, SpiritDirectoryCard
from app.providers.errors import ProviderError


class ClaimState(Enum):
    INSERTED = "inserted"
    REPLAY = "replay"
    CONFLICT = "conflict"
    BUSY = "busy"


@dataclass(frozen=True)
class SentMessage:
    thread_id: UUID
    message_id: UUID
    seq: int
    text: str
    peer_spirit_id: UUID
    peer_display_name: str
    peer_avatar_url: str
    peer_available: bool
    created_at: datetime


@dataclass(frozen=True)
class ClaimResult:
    state: ClaimState
    sent: SentMessage | None = None


class Claims(Protocol):
    async def claim(self, user_id: UUID, client_id: UUID, request_hash: str) -> ClaimResult: ...

    async def release(self, user_id: UUID, client_id: UUID, request_hash: str) -> None: ...


class OwnCards(Protocol):
    async def own(self, user_id: UUID) -> SpiritDirectoryCard | None: ...


class PeerDirectory(Protocol):
    async def lookup(self, viewer_id: UUID, spirit_id: UUID) -> DirectoryAnswer: ...


class ThreadIndex(Protocol):
    async def thread_id(self, low_id: UUID, high_id: UUID) -> UUID | None: ...


class TextSafety(Protocol):
    async def decision(self, text: str) -> str: ...


class SendWriter(Protocol):
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
    ) -> SentMessage: ...


async def send_message(
    claims: Claims,
    cards: OwnCards,
    directory: PeerDirectory,
    threads: ThreadIndex,
    safety: TextSafety,
    writer: SendWriter,
    *,
    user_id: UUID,
    client_id: UUID,
    peer_spirit_id: UUID,
    text: str,
    now: datetime,
) -> SentMessage:
    try:
        body = message_text(text)
    except ChatTextRejected:
        raise _invalid() from None
    digest = send_hash(peer_spirit_id, body)
    claim = await claims.claim(user_id, client_id, digest)
    if claim.state is ClaimState.REPLAY:
        if claim.sent is None:
            raise _missing()
        return claim.sent
    if claim.state is ClaimState.CONFLICT:
        raise _conflict()
    if claim.state is ClaimState.BUSY:
        raise _busy()
    try:
        return await _send_new(
            cards,
            directory,
            threads,
            safety,
            writer,
            user_id=user_id,
            client_id=client_id,
            peer_spirit_id=peer_spirit_id,
            body=body,
            digest=digest,
            now=now,
        )
    except Exception:
        await claims.release(user_id, client_id, digest)
        raise


async def _send_new(
    cards: OwnCards,
    directory: PeerDirectory,
    threads: ThreadIndex,
    safety: TextSafety,
    writer: SendWriter,
    *,
    user_id: UUID,
    client_id: UUID,
    peer_spirit_id: UUID,
    body: str,
    digest: str,
    now: datetime,
) -> SentMessage:
    own = await cards.own(user_id)
    if own is None or own.spirit_id == peer_spirit_id:
        raise _missing()
    looked = await directory.lookup(user_id, peer_spirit_id)
    low_id, high_id = ordered_spirit_pair(own.spirit_id, peer_spirit_id)
    existing = await threads.thread_id(low_id, high_id)
    if looked.outcome is not DirectoryOutcome.AVAILABLE or looked.card is None:
        if existing is None:
            raise _missing()
        raise _unavailable()
    _require_allow(await _decision(safety, body))
    return await writer.commit_send(
        user_id=user_id,
        client_id=client_id,
        request_hash=digest,
        own=own,
        peer=looked.card,
        text=body,
        created_at=now,
    )


async def _decision(safety: TextSafety, text: str) -> str:
    try:
        return await safety.decision(text)
    except ProviderError as exc:
        if exc.code == "PROVIDER_TIMEOUT":
            raise _error("PROVIDER_TIMEOUT", 504, retryable=True) from exc
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True) from exc


def _require_allow(decision: str) -> None:
    if decision == "block":
        raise _error("MODERATION_REJECTED", 422)
    if decision != "allow":
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True)


def _missing() -> ApiError:
    return _error("NOT_FOUND", 404)


def _unavailable() -> ApiError:
    return _error("SPIRIT_UNAVAILABLE", 409)


def _invalid() -> ApiError:
    return _error("INVALID_INPUT", 422)


def _conflict() -> ApiError:
    return _error("IDEMPOTENCY_CONFLICT", 409)


def _busy() -> ApiError:
    return _error("IDEMPOTENCY_IN_PROGRESS", 409, retryable=True)


def _error(code: str, status_code: int, *, retryable: bool = False) -> ApiError:
    return ApiError(code, public_error_message(code), status_code=status_code, retryable=retryable)


class ReadStore(Protocol):
    async def mark_read(self, user_id: UUID, thread_id: UUID) -> ReadMark | None: ...


class ClearStore(Protocol):
    async def apply_clear(
        self,
        *,
        user_id: UUID,
        client_id: UUID,
        request_hash: str,
        thread_id: UUID,
    ) -> ClearMark | None: ...


@dataclass(frozen=True)
class ClearClaim:
    state: ClaimState
    mark: ClearMark | None = None


class ClearClaims(Protocol):
    async def claim(self, user_id: UUID, client_id: UUID, request_hash: str) -> ClearClaim: ...

    async def release(self, user_id: UUID, client_id: UUID, request_hash: str) -> None: ...


async def mark_thread_read(store: ReadStore, *, user_id: UUID, thread_id: UUID) -> ReadMark:
    found = await store.mark_read(user_id, thread_id)
    if found is None:
        raise _missing()
    return found


async def clear_thread(
    claims: ClearClaims,
    store: ClearStore,
    *,
    user_id: UUID,
    client_id: UUID,
    thread_id: UUID,
) -> ClearMark:
    digest = clear_hash(thread_id)
    claim = await claims.claim(user_id, client_id, digest)
    if claim.state is ClaimState.REPLAY:
        if claim.mark is None:
            raise _missing()
        return claim.mark
    if claim.state is ClaimState.CONFLICT:
        raise _conflict()
    if claim.state is ClaimState.BUSY:
        raise _busy()
    try:
        found = await store.apply_clear(
            user_id=user_id,
            client_id=client_id,
            request_hash=digest,
            thread_id=thread_id,
        )
        if found is None:
            raise _missing()
        return found
    except Exception:
        await claims.release(user_id, client_id, digest)
        raise
