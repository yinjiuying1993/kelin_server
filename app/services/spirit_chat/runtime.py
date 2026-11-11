"""Connect the socket commands to short transactions. Delivery stays in-process."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.db.session import claimed_read_transaction, claimed_transaction
from app.domain.spirit_chat import ClearMark, ReadMark
from app.domain.spirit_directory import SpiritDirectoryCard
from app.providers.errors import ProviderError
from app.repositories.spirit_chat import SqlSpiritChat
from app.repositories.spirit_directory import SqlSpiritDirectory
from app.services.spirit_chat.command import (
    ClaimResult,
    ClaimState,
    ClearClaim,
    SentMessage,
    clear_thread,
    mark_thread_read,
    send_message,
)
from app.services.spirit_chat.delivery import push_deliveries
from app.services.spirit_chat.hub import Hub
from app.services.spirit_chat.query import delivery_visible
from app.services.spirit_directory import lookup_directory

_viewer: ContextVar[UUID | None] = ContextVar("spirit_chat_viewer", default=None)


class RuntimeCommands:
    def __init__(self, factory: async_sessionmaker[AsyncSession], provider: object) -> None:
        self._factory = factory
        self._provider = provider

    async def send(
        self, *, user_id: UUID, client_id: UUID, peer_spirit_id: UUID, text: str
    ) -> SentMessage:
        token = _viewer.set(user_id)
        try:
            return await send_message(
                _Claims(self._factory),
                _Cards(self._factory),
                _Directory(self._factory),
                _Threads(self._factory),
                _Safety(self._provider),
                _Writer(self._factory),
                user_id=user_id,
                client_id=client_id,
                peer_spirit_id=peer_spirit_id,
                text=text,
                now=datetime.now(UTC),
            )
        finally:
            _viewer.reset(token)

    async def read(self, *, user_id: UUID, thread_id: UUID) -> ReadMark:
        async with claimed_transaction(self._factory, CurrentUser(id=user_id)) as session:
            return await mark_thread_read(
                SqlSpiritChat(session), user_id=user_id, thread_id=thread_id
            )

    async def clear(self, *, user_id: UUID, client_id: UUID, thread_id: UUID) -> ClearMark:
        return await clear_thread(
            _ClearClaims(self._factory),
            _ClearStore(self._factory),
            user_id=user_id,
            client_id=client_id,
            thread_id=thread_id,
        )


async def deliver_available(factory: async_sessionmaker[AsyncSession], hub: Hub) -> int:
    async with claimed_transaction(factory, None) as session:
        rows = await SqlSpiritChat(session).take_outbox(50)
    return await push_deliveries(rows, visible=_Visible(factory), hub=hub)


async def deliver_loop(
    factory: async_sessionmaker[AsyncSession], hub: Hub, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            await deliver_available(factory, hub)
        except Exception:
            get_logger(capability="spirit_chat").info(
                "spirit_chat_deliver",
                error_code="DEPENDENCY_UNAVAILABLE",
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            continue


class _Claims:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def claim(self, user_id: UUID, client_id: UUID, request_hash: str) -> ClaimResult:
        async with claimed_transaction(self._factory, CurrentUser(id=user_id)) as session:
            store = SqlSpiritChat(session)
            state, row = await store.claim_outcome(user_id, client_id, request_hash)
            if state == "inserted":
                return ClaimResult(ClaimState.INSERTED)
            if state == "conflict":
                return ClaimResult(ClaimState.CONFLICT)
            if state == "busy":
                return ClaimResult(ClaimState.BUSY)
            if row.message_id is None:
                return ClaimResult(ClaimState.REPLAY, None)
            loaded = await store.load_sent(user_id, row.message_id)
            return ClaimResult(ClaimState.REPLAY, _sent(loaded))

    async def release(self, user_id: UUID, client_id: UUID, request_hash: str) -> None:
        async with claimed_transaction(self._factory, CurrentUser(id=user_id)) as session:
            await SqlSpiritChat(session).release(user_id, client_id, request_hash)


class _Writer:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

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
        del created_at
        async with claimed_transaction(self._factory, CurrentUser(id=user_id)) as session:
            store = SqlSpiritChat(session)
            row = await store.commit_send(
                own_spirit=own.spirit_id,
                peer_spirit=peer.spirit_id,
                body=text,
                peer_name=peer.display_name,
                peer_avatar=peer.avatar_url,
                peer_available=peer.available,
                own_name=own.display_name,
                own_avatar=own.avatar_url,
            )
            sent = SentMessage(
                thread_id=row.thread_id,
                message_id=row.message_id,
                seq=int(row.seq),
                text=text,
                peer_spirit_id=peer.spirit_id,
                peer_display_name=peer.display_name,
                peer_avatar_url=peer.avatar_url,
                peer_available=peer.available,
                created_at=row.created_at,
            )
            await store.complete(
                user_id=user_id,
                client_id=client_id,
                request_hash=request_hash,
                thread_id=sent.thread_id,
                message_id=sent.message_id,
                message_seq=sent.seq,
            )
            return sent


class _Cards:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def own(self, user_id: UUID) -> SpiritDirectoryCard | None:
        async with claimed_read_transaction(self._factory, CurrentUser(id=user_id)) as session:
            face = await SqlSpiritChat(session).own_face(user_id)
        if face is None:
            return None
        return SpiritDirectoryCard(
            spirit_id=face.spirit_id,
            owner_user_id=user_id,
            display_name=face.display_name,
            avatar_url=face.avatar_url,
            available=True,
        )


class _Directory:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def lookup(self, viewer_id: UUID, spirit_id: UUID):
        async with claimed_read_transaction(self._factory, CurrentUser(id=viewer_id)) as session:
            return await lookup_directory(
                SqlSpiritDirectory(session), viewer_id=viewer_id, spirit_id=spirit_id
            )


class _Threads:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def thread_id(self, low_id: UUID, high_id: UUID) -> UUID | None:
        viewer = _viewer.get()
        user = None if viewer is None else CurrentUser(id=viewer)
        async with claimed_read_transaction(self._factory, user) as session:
            return await SqlSpiritChat(session).find_thread(low_id, high_id)


class _Safety:
    def __init__(self, provider: object) -> None:
        self._provider = provider

    async def decision(self, text: str) -> str:
        moderate = getattr(self._provider, "moderate_text", None)
        if moderate is None:
            raise ProviderError("DEPENDENCY_UNAVAILABLE")
        return await moderate(text)


class _ClearClaims:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def claim(self, user_id: UUID, client_id: UUID, request_hash: str) -> ClearClaim:
        async with claimed_transaction(self._factory, CurrentUser(id=user_id)) as session:
            state, mark = await SqlSpiritChat(session).claim_clear(
                user_id, client_id, request_hash
            )
        if state == "inserted":
            return ClearClaim(ClaimState.INSERTED)
        if state == "conflict":
            return ClearClaim(ClaimState.CONFLICT)
        if state == "busy":
            return ClearClaim(ClaimState.BUSY)
        return ClearClaim(ClaimState.REPLAY, mark)

    async def release(self, user_id: UUID, client_id: UUID, request_hash: str) -> None:
        async with claimed_transaction(self._factory, CurrentUser(id=user_id)) as session:
            await SqlSpiritChat(session).release(user_id, client_id, request_hash)


class _ClearStore:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def apply_clear(
        self,
        *,
        user_id: UUID,
        client_id: UUID,
        request_hash: str,
        thread_id: UUID,
    ) -> ClearMark | None:
        async with claimed_transaction(self._factory, CurrentUser(id=user_id)) as session:
            return await SqlSpiritChat(session).apply_clear(
                user_id=user_id,
                client_id=client_id,
                request_hash=request_hash,
                thread_id=thread_id,
            )


class _Visible:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def __call__(self, user_id: UUID, thread_id: UUID, seq: int, hidden: bool) -> bool:
        async with claimed_read_transaction(self._factory, CurrentUser(id=user_id)) as session:
            clear_seq = await SqlSpiritChat(session).clear_seq(user_id, thread_id)
        return delivery_visible(clear_seq=clear_seq, seq=seq, hidden=hidden)


def _sent(row) -> SentMessage:
    return SentMessage(
        thread_id=row.thread_id,
        message_id=row.id,
        seq=int(row.seq),
        text=str(row.body),
        peer_spirit_id=row.peer_spirit_id,
        peer_display_name=str(row.peer_display_name),
        peer_avatar_url=str(row.peer_avatar_url),
        peer_available=bool(row.peer_available),
        created_at=row.created_at,
    )
