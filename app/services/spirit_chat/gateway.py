"""One user socket. Business refusals stay in the frame, not the close code."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from starlette.websockets import WebSocketDisconnect

from app.core.errors import ApiError
from app.core.logging import get_logger
from app.domain.cursor import utc_iso
from app.domain.spirit_chat import ClearMark, ReadMark
from app.services.spirit_chat.command import SentMessage
from app.services.spirit_chat.hub import Hub

COMMANDS_PER_WINDOW = 30
COMMAND_WINDOW = timedelta(seconds=10)
HEARTBEAT_TIMEOUT = timedelta(seconds=60)
AUTH_CLOSE = 4401
EXPIRED_CLOSE = 4402
HEARTBEAT_CLOSE = 4403
REPLACED_CLOSE = 1000
_COMMANDS = frozenset({"message.send", "thread.read", "thread.clear"})


class ChatCommands(Protocol):
    async def send(
        self, *, user_id: UUID, client_id: UUID, peer_spirit_id: UUID, text: str
    ) -> SentMessage: ...

    async def read(self, *, user_id: UUID, thread_id: UUID) -> ReadMark: ...

    async def clear(self, *, user_id: UUID, client_id: UUID, thread_id: UUID) -> ClearMark: ...


class RateGate:
    def __init__(
        self,
        limit: int = COMMANDS_PER_WINDOW,
        window: timedelta = COMMAND_WINDOW,
    ) -> None:
        self._limit = limit
        self._window = window
        self._hits: dict[UUID, list[datetime]] = {}

    def allow(self, user_id: UUID, now: datetime) -> bool:
        recent = [item for item in self._hits.get(user_id, []) if now - item < self._window]
        if len(recent) >= self._limit:
            self._hits[user_id] = recent
            return False
        recent.append(now)
        self._hits[user_id] = recent
        return True


def log_frame(*, kind: str, thread_id: str | None, error_code: str | None) -> None:
    get_logger(capability="spirit_chat").info(
        "spirit_chat_frame",
        frame_type=kind,
        thread_id=thread_id,
        error_code=error_code,
    )


def frame(kind: str, event_id: str | None, thread_id: str | None, data: dict) -> dict:
    return {"type": kind, "event_id": event_id, "thread_id": thread_id, "data": data}


async def dispatch(
    commands: ChatCommands,
    gate: RateGate,
    *,
    user_id: UUID,
    payload: object,
    now: datetime,
) -> dict | None:
    if not isinstance(payload, dict):
        return _error_frame(None, "INVALID_INPUT")
    kind = payload.get("type")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if kind == "ping":
        log_frame(kind="ping", thread_id=None, error_code=None)
        return frame("pong", str(uuid4()), None, {})
    if kind not in _COMMANDS:
        return _error_frame(None, "INVALID_INPUT")
    thread_id = _text_id(data.get("thread_id"))
    if not gate.allow(user_id, now):
        return _error_frame(thread_id, "RATE_LIMITED", kind=str(kind))
    try:
        if kind == "message.send":
            return await _send(commands, user_id=user_id, data=data)
        if kind == "thread.read":
            await _read(commands, user_id=user_id, data=data)
            log_frame(kind="thread.read", thread_id=thread_id, error_code=None)
            return None
        await _clear(commands, user_id=user_id, data=data)
        log_frame(kind="thread.clear", thread_id=thread_id, error_code=None)
        return None
    except ApiError as exc:
        client_id = _uuid(data.get("client_id"))
        log_frame(kind=str(kind), thread_id=thread_id, error_code=exc.code)
        if kind == "message.send" and client_id is not None:
            return frame("message.ack", str(client_id), None, {"error": {"code": exc.code}})
        return _error_frame(thread_id, exc.code, kind=str(kind), logged=True)


async def serve_connection(
    socket,
    *,
    user_id: UUID,
    expires_at: datetime | None,
    commands: ChatCommands,
    hub: Hub,
    gate: RateGate,
    deliver: Callable[[], Awaitable[None]],
    now: Callable[[], datetime],
    receive_timeout: Callable[[float], Awaitable[str]],
) -> None:
    if expires_at is not None and now() >= expires_at:
        await socket.close(EXPIRED_CLOSE)
        return
    await socket.accept()
    replaced = await hub.attach(user_id, socket)
    if replaced is not None:
        await replaced.close(REPLACED_CLOSE)
    deadline = now() + HEARTBEAT_TIMEOUT
    try:
        while True:
            if expires_at is not None and now() >= expires_at:
                await socket.close(EXPIRED_CLOSE)
                return
            remaining = (deadline - now()).total_seconds()
            if remaining <= 0:
                await socket.close(HEARTBEAT_CLOSE)
                return
            try:
                raw = await receive_timeout(remaining)
            except TimeoutError:
                await socket.close(HEARTBEAT_CLOSE)
                return
            except WebSocketDisconnect:
                return
            payload = _loads(raw)
            if isinstance(payload, dict) and payload.get("type") == "ping":
                deadline = now() + HEARTBEAT_TIMEOUT
            reply = await dispatch(commands, gate, user_id=user_id, payload=payload, now=now())
            if reply is not None:
                await socket.send_json(reply)
            asyncio.create_task(_safe_deliver(deliver))
    finally:
        await hub.detach(user_id, socket)


async def _send(commands: ChatCommands, *, user_id: UUID, data: dict) -> dict:
    client_id = _uuid(data.get("client_id"))
    peer = _uuid(data.get("peer_spirit_id"))
    if client_id is None or peer is None or not isinstance(data.get("text"), str):
        raise _invalid()
    sent = await commands.send(
        user_id=user_id, client_id=client_id, peer_spirit_id=peer, text=data["text"]
    )
    log_frame(kind="message.send", thread_id=str(sent.thread_id), error_code=None)
    return frame(
        "message.ack",
        str(client_id),
        str(sent.thread_id),
        {
            "message_id": str(sent.message_id),
            "seq": sent.seq,
            "text": sent.text,
            "created_at": utc_iso(sent.created_at),
            "peer_spirit_id": str(sent.peer_spirit_id),
            "display_name": sent.peer_display_name,
            "avatar_url": sent.peer_avatar_url,
            "available": sent.peer_available,
        },
    )


async def _read(commands: ChatCommands, *, user_id: UUID, data: dict) -> None:
    thread_id = _uuid(data.get("thread_id"))
    if thread_id is None:
        raise _invalid()
    await commands.read(user_id=user_id, thread_id=thread_id)


async def _clear(commands: ChatCommands, *, user_id: UUID, data: dict) -> None:
    client_id = _uuid(data.get("client_id"))
    thread_id = _uuid(data.get("thread_id"))
    if client_id is None or thread_id is None:
        raise _invalid()
    await commands.clear(user_id=user_id, client_id=client_id, thread_id=thread_id)


def _error_frame(
    thread_id: str | None,
    code: str,
    *,
    kind: str = "error",
    logged: bool = False,
) -> dict:
    if not logged:
        log_frame(kind=kind, thread_id=thread_id, error_code=code)
    return frame("error", str(uuid4()), thread_id, {"error": {"code": code}})


def _invalid() -> ApiError:
    return ApiError("INVALID_INPUT", "invalid input", status_code=422)


def _uuid(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _text_id(value: object) -> str | None:
    parsed = _uuid(value)
    return None if parsed is None else str(parsed)


def _loads(raw: str) -> object:
    import json

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def _safe_deliver(deliver: Callable[[], Awaitable[None]]) -> None:
    try:
        await deliver()
    except Exception:
        log_frame(kind="deliver", thread_id=None, error_code="DEPENDENCY_UNAVAILABLE")
