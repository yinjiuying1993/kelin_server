"""Central event idempotency. event_id is stored as client_id."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CENTRAL_OPERATION = "central.event"


@dataclass(frozen=True, slots=True)
class CentralIdempotencyClaim:
    inserted: bool
    request_hash: str
    status: str
    response_json: dict[str, Any] | None


def _as_object(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    return None


async def claim_event(
    session: AsyncSession,
    owner_id: uuid.UUID,
    event_id: uuid.UUID,
    request_hash: str,
) -> CentralIdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": CENTRAL_OPERATION,
        "client_id": event_id,
        "request_hash": request_hash,
    }
    for _ in range(3):
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO public.idempotency_records ("
                    "user_id, operation, client_id, request_hash, status"
                    ") VALUES ("
                    ":user_id, :operation, :client_id, :request_hash, 'in_progress'"
                    ") ON CONFLICT (user_id, operation, client_id) DO NOTHING "
                    "RETURNING request_hash, status, response_json"
                ),
                params,
            )
        ).first()
        if inserted is not None:
            return CentralIdempotencyClaim(
                inserted=True,
                request_hash=str(inserted.request_hash),
                status=str(inserted.status),
                response_json=_as_object(inserted.response_json),
            )
        existing = (
            await session.execute(
                text(
                    "SELECT request_hash, status, response_json "
                    "FROM public.idempotency_records "
                    "WHERE user_id = :user_id AND operation = :operation "
                    "AND client_id = :client_id "
                    "FOR UPDATE"
                ),
                params,
            )
        ).first()
        if existing is not None:
            return CentralIdempotencyClaim(
                inserted=False,
                request_hash=str(existing.request_hash),
                status=str(existing.status),
                response_json=_as_object(existing.response_json),
            )
    raise RuntimeError("central idempotency claim failed")


async def complete_event(
    session: AsyncSession,
    owner_id: uuid.UUID,
    event_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = 'central', "
            "response_json = CAST(:payload AS jsonb), completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": CENTRAL_OPERATION,
            "client_id": event_id,
            "payload": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        },
    )
