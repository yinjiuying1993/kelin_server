"""V4-T07 keeps one socket and delivers outbox in this process."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.domain.spirit_chat import ClearMark, OutboxDelivery, ReadMark
from app.services.spirit_chat.command import SentMessage
from app.services.spirit_chat.delivery import client_event, push_deliveries
from app.services.spirit_chat.gateway import (
    AUTH_CLOSE,
    EXPIRED_CLOSE,
    HEARTBEAT_CLOSE,
    REPLACED_CLOSE,
    RateGate,
    dispatch,
    serve_connection,
)
from app.services.spirit_chat.hub import Hub
from starlette.websockets import WebSocketDisconnect

_USER = UUID("00000000-0000-4000-8000-0000000000a1")
_PEER_USER = UUID("00000000-0000-4000-8000-0000000000a2")
_OFFLINE = UUID("00000000-0000-4000-8000-0000000000a3")
_THREAD = UUID("00000000-0000-4000-8000-0000000000a4")
_CLIENT = UUID("00000000-0000-4000-8000-0000000000a5")
_SPIRIT = UUID("00000000-0000-4000-8000-0000000000a6")
_MESSAGE = UUID("00000000-0000-4000-8000-0000000000a7")
_NOW = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


class _Sock:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict] = []
        self.closed: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000) -> None:
        self.closed = code


class _Commands:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def send(self, *, user_id, client_id, peer_spirit_id, text):
        self.calls.append("send")
        if text == "坏":
            raise ApiError("NOT_FOUND", "not found", status_code=404)
        return SentMessage(
            _THREAD,
            _MESSAGE,
            2,
            text,
            peer_spirit_id,
            "对方",
            "https://kelin.invalid/peer",
            True,
            _NOW,
        )

    async def read(self, *, user_id, thread_id):
        self.calls.append("read")
        return ReadMark(thread_id, 2, 0, 0)

    async def clear(self, *, user_id, client_id, thread_id):
        self.calls.append("clear")
        return ClearMark(thread_id, 2)


def _send_payload(text: str = "你好") -> dict:
    return {
        "type": "message.send",
        "data": {
            "client_id": str(_CLIENT),
            "peer_spirit_id": str(_SPIRIT),
            "text": text,
        },
    }


def _row(recipient: UUID, kind: str, seq: int, body: dict, row_id: UUID) -> OutboxDelivery:
    return OutboxDelivery(row_id, recipient, _THREAD, kind, seq, json.dumps(body))


@pytest.mark.asyncio
async def test_unknown_and_rate_limit_do_not_call_commands() -> None:
    commands = _Commands()
    gate = RateGate(limit=1)
    unknown = await dispatch(
        commands, gate, user_id=_USER, payload={"type": "message.delete"}, now=_NOW
    )
    assert unknown["type"] == "error"
    assert unknown["data"]["error"]["code"] == "INVALID_INPUT"
    assert commands.calls == []
    await dispatch(commands, gate, user_id=_USER, payload=_send_payload(), now=_NOW)
    limited = await dispatch(
        commands, gate, user_id=_USER, payload=_send_payload("再一条"), now=_NOW
    )
    assert limited["data"]["error"]["code"] == "RATE_LIMITED"
    assert commands.calls == ["send"]


@pytest.mark.asyncio
async def test_send_ack_stays_on_the_socket_when_refused() -> None:
    commands = _Commands()
    ack = await dispatch(
        commands, RateGate(), user_id=_USER, payload=_send_payload(), now=_NOW
    )
    assert ack["type"] == "message.ack"
    assert ack["event_id"] == str(_CLIENT)
    assert ack["data"]["text"] == "你好"
    assert "user_id" not in ack["data"]
    refused = await dispatch(
        commands, RateGate(), user_id=_USER, payload=_send_payload("坏"), now=_NOW
    )
    assert refused["type"] == "message.ack"
    assert refused["data"]["error"]["code"] == "NOT_FOUND"
    ping = await dispatch(
        commands, RateGate(limit=1), user_id=_USER, payload={"type": "ping"}, now=_NOW
    )
    assert ping["type"] == "pong"
    assert commands.calls == ["send", "send"]


@pytest.mark.asyncio
async def test_read_and_clear_use_the_same_command_port() -> None:
    commands = _Commands()
    read = await dispatch(
        commands,
        RateGate(),
        user_id=_USER,
        payload={"type": "thread.read", "data": {"thread_id": str(_THREAD)}},
        now=_NOW,
    )
    cleared = await dispatch(
        commands,
        RateGate(),
        user_id=_USER,
        payload={
            "type": "thread.clear",
            "data": {"client_id": str(_CLIENT), "thread_id": str(_THREAD)},
        },
        now=_NOW,
    )
    assert read is None
    assert cleared is None
    assert commands.calls == ["read", "clear"]


@pytest.mark.asyncio
async def test_delivery_reaches_only_an_online_visible_socket() -> None:
    hub = Hub()
    peer = _Sock()
    sender = _Sock()
    await hub.attach(_PEER_USER, peer)
    await hub.attach(_USER, sender)
    created = _row(
        _PEER_USER,
        "message.created",
        2,
        {"message_id": str(_MESSAGE), "seq": 2, "text": "你好", "mine": False},
        UUID("00000000-0000-4000-8000-0000000000b1"),
    )
    peer_row = _row(
        _PEER_USER,
        "thread.updated",
        2,
        {"text": "你好", "unread": 1, "peer_spirit_id": str(_SPIRIT), "hidden": False},
        UUID("00000000-0000-4000-8000-0000000000b2"),
    )
    mine = _row(
        _USER,
        "thread.updated",
        2,
        {"text": "你好", "unread": 0, "hidden": False},
        UUID("00000000-0000-4000-8000-0000000000b3"),
    )
    hidden = _row(
        _USER,
        "thread.updated",
        2,
        {"text": "", "unread": 0, "hidden": True},
        UUID("00000000-0000-4000-8000-0000000000b4"),
    )
    stale = _row(
        _PEER_USER,
        "message.created",
        1,
        {"text": "旧", "mine": False},
        UUID("00000000-0000-4000-8000-0000000000b5"),
    )
    offline = _row(
        _OFFLINE,
        "message.created",
        2,
        {"text": "你好", "mine": False},
        UUID("00000000-0000-4000-8000-0000000000b6"),
    )

    async def visible(user_id, thread_id, seq, hidden_flag) -> bool:
        del thread_id
        if hidden_flag:
            return user_id == _USER
        return seq > 1

    pushed = await push_deliveries(
        [created, peer_row, mine, hidden, stale, offline], visible=visible, hub=hub
    )
    assert pushed == 4
    assert [item["type"] for item in peer.sent] == ["message.created", "thread.updated"]
    assert [item["type"] for item in sender.sent] == ["thread.updated", "thread.updated"]
    assert client_event(created)["data"]["text"] == "你好"
    assert "user_id" not in json.dumps(peer.sent)


@pytest.mark.asyncio
async def test_close_codes_are_only_auth_expiry_heartbeat_or_replacement() -> None:
    hub = Hub()
    old = _Sock()
    await hub.attach(_USER, old)
    socket = _Sock()

    async def stop(_seconds: float) -> str:
        raise WebSocketDisconnect

    await serve_connection(
        socket,
        user_id=_USER,
        expires_at=None,
        commands=_Commands(),
        hub=hub,
        gate=RateGate(),
        deliver=_noop,
        now=lambda: _NOW,
        receive_timeout=stop,
    )
    assert old.closed == REPLACED_CLOSE
    assert socket.closed is None
    assert socket.accepted is True

    expired = _Sock()

    async def unused(_seconds: float) -> str:
        raise AssertionError("expired sockets do not read")

    await serve_connection(
        expired,
        user_id=_USER,
        expires_at=_NOW - timedelta(seconds=1),
        commands=_Commands(),
        hub=Hub(),
        gate=RateGate(),
        deliver=_noop,
        now=lambda: _NOW,
        receive_timeout=unused,
    )
    assert expired.closed == EXPIRED_CLOSE
    assert expired.accepted is False

    quiet = _Sock()

    async def timeout(_seconds: float) -> str:
        raise TimeoutError

    await serve_connection(
        quiet,
        user_id=_USER,
        expires_at=None,
        commands=_Commands(),
        hub=Hub(),
        gate=RateGate(),
        deliver=_noop,
        now=lambda: _NOW,
        receive_timeout=timeout,
    )
    assert quiet.closed == HEARTBEAT_CLOSE
    assert AUTH_CLOSE == 4401


def test_delivery_is_not_the_rating_worker() -> None:
    root = Path(__file__).resolve().parents[2]
    worker = (root / "app" / "worker.py").read_text(encoding="utf-8")
    cron = (root / "app" / "services" / "cron.py").read_text(encoding="utf-8")
    delivery = (
        root / "app" / "services" / "spirit_chat" / "delivery.py"
    ).read_text(encoding="utf-8")
    migration = root / "app" / "db" / "migrations" / "versions"
    migration = migration / "20261110_0135_spirit_chat_deliver.py"
    source = migration.read_text(encoding="utf-8")
    assert "spirit_chat" not in worker
    assert "spirit_chat" not in cron
    assert "run_worker_tick" not in delivery
    assert "outbox_events" not in delivery
    assert "TO kelin_api" in source
    assert "kelin_worker" not in source
    assert "text" not in __import__(
        "app.services.spirit_chat.gateway", fromlist=["log_frame"]
    ).log_frame.__code__.co_varnames


async def _noop() -> None:
    return None
