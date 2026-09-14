"""ASR idempotency on public.idempotency_records. Spec §10.4."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.speech import ASR_TRANSCRIBE_OPERATION, TTS_SYNTHESIZE_OPERATION


@dataclass(frozen=True, slots=True)
class SpeechIdempotencyClaim:
    inserted: bool
    request_hash: str
    status: str
    response_json: dict[str, Any] | None


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("speech cannot write in a read-only transaction")


async def claim_transcribe_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> SpeechIdempotencyClaim:
    return await _claim_idempotency(
        session, owner_id, client_id, request_hash, operation=ASR_TRANSCRIBE_OPERATION
    )


async def abandon_transcribe_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
) -> None:
    await _abandon_idempotency(session, owner_id, client_id, operation=ASR_TRANSCRIBE_OPERATION)


async def complete_transcribe_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    await _complete_idempotency(
        session,
        owner_id,
        client_id,
        payload,
        operation=ASR_TRANSCRIBE_OPERATION,
        resource_type="transcript",
    )


async def claim_synthesize_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> SpeechIdempotencyClaim:
    return await _claim_idempotency(
        session, owner_id, client_id, request_hash, operation=TTS_SYNTHESIZE_OPERATION
    )


async def abandon_synthesize_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
) -> None:
    await _abandon_idempotency(session, owner_id, client_id, operation=TTS_SYNTHESIZE_OPERATION)


async def complete_synthesize_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    await _complete_idempotency(
        session,
        owner_id,
        client_id,
        payload,
        operation=TTS_SYNTHESIZE_OPERATION,
        resource_type="speech_audio",
    )


@dataclass(frozen=True, slots=True)
class OwnedSpiritMessage:
    id: uuid.UUID
    spirit_id: uuid.UUID
    role: str
    content: str
    status: str


async def fetch_owned_spirit_message(
    session: AsyncSession,
    owner_id: uuid.UUID,
    message_id: uuid.UUID,
) -> OwnedSpiritMessage | None:
    row = (
        await session.execute(
            text(
                "SELECT m.id, m.spirit_id, m.role, m.content, m.status "
                "FROM public.messages m "
                "JOIN public.spirits s ON s.id = m.spirit_id "
                "WHERE m.id = :message_id AND s.user_id = :owner_id"
            ),
            {"message_id": message_id, "owner_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return OwnedSpiritMessage(
        id=row.id,
        spirit_id=row.spirit_id,
        role=str(row.role),
        content=str(row.content),
        status=str(row.status),
    )


async def _claim_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
    *,
    operation: str,
) -> SpeechIdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": operation,
        "client_id": client_id,
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
            return SpeechIdempotencyClaim(
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
            return SpeechIdempotencyClaim(
                inserted=False,
                request_hash=str(existing.request_hash),
                status=str(existing.status),
                response_json=_as_object(existing.response_json),
            )
    raise RuntimeError("speech idempotency claim failed")


async def _abandon_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    *,
    operation: str,
) -> None:
    await session.execute(
        text(
            "DELETE FROM public.idempotency_records "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id AND status = 'in_progress'"
        ),
        {
            "user_id": owner_id,
            "operation": operation,
            "client_id": client_id,
        },
    )


async def _complete_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    payload: dict[str, Any],
    *,
    operation: str,
    resource_type: str,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = :resource_type, "
            "response_json = CAST(:payload AS jsonb), completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": operation,
            "client_id": client_id,
            "resource_type": resource_type,
            "payload": json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
        },
    )


def _as_object(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value:
        loaded = json.loads(value)
        if isinstance(loaded, dict):
            return loaded
    return None
