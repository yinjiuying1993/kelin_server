"""Push already-written outbox rows. Offline rows stay processed."""

from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

from app.core.logging import get_logger
from app.domain.spirit_chat import OutboxDelivery
from app.services.spirit_chat.hub import Hub
from app.services.spirit_chat.query import delivery_visible


class Visible(Protocol):
    async def __call__(self, user_id: UUID, thread_id: UUID, seq: int, hidden: bool) -> bool: ...


def client_event(row: OutboxDelivery) -> dict[str, object]:
    data = _object(row.event_body)
    clean = {key: value for key, value in data.items() if "user_id" not in key.lower()}
    return {
        "type": row.event_type,
        "event_id": str(row.id),
        "thread_id": str(row.thread_id),
        "data": clean,
    }


async def push_deliveries(rows: list[OutboxDelivery], *, visible: Visible, hub: Hub) -> int:
    pushed = 0
    for row in rows:
        hidden = _object(row.event_body).get("hidden") is True
        if not await visible(row.recipient_user_id, row.thread_id, row.seq, hidden):
            continue
        socket = hub.current(row.recipient_user_id)
        if socket is None:
            continue
        await socket.send_json(client_event(row))
        pushed += 1
    if pushed:
        get_logger(capability="spirit_chat").info("spirit_chat_deliver", pushed=pushed)
    return pushed


def still_visible(clear_seq: int | None, seq: int, hidden: bool) -> bool:
    return delivery_visible(clear_seq=clear_seq, seq=seq, hidden=hidden)


def _object(body: str) -> dict[str, object]:
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload
