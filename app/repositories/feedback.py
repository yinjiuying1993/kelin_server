"""feedback_submissions and the shared idempotency row for feedback.submit."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feedback import FEEDBACK_OPERATION, FEEDBACK_RESOURCE_TYPE


@dataclass(frozen=True, slots=True)
class FeedbackClaim:
    inserted: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None
    response_json: dict[str, Any] | None


def _as_object(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return None


async def claim_feedback(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> FeedbackClaim:
    params = {
        "user_id": owner_id,
        "operation": FEEDBACK_OPERATION,
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
                    "RETURNING request_hash, status, resource_id, response_json"
                ),
                params,
            )
        ).first()
        if inserted is not None:
            return FeedbackClaim(
                inserted=True,
                request_hash=str(inserted.request_hash),
                status=str(inserted.status),
                resource_id=inserted.resource_id,
                response_json=_as_object(inserted.response_json),
            )
        existing = (
            await session.execute(
                text(
                    "SELECT request_hash, status, resource_id, response_json "
                    "FROM public.idempotency_records "
                    "WHERE user_id = :user_id AND operation = :operation "
                    "AND client_id = :client_id "
                    "FOR UPDATE"
                ),
                params,
            )
        ).first()
        if existing is not None:
            return FeedbackClaim(
                inserted=False,
                request_hash=str(existing.request_hash),
                status=str(existing.status),
                resource_id=existing.resource_id,
                response_json=_as_object(existing.response_json),
            )
    raise RuntimeError("feedback idempotency claim failed")


async def lock_feedback_claim(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
) -> FeedbackClaim | None:
    row = (
        await session.execute(
            text(
                "SELECT request_hash, status, resource_id, response_json "
                "FROM public.idempotency_records "
                "WHERE user_id = :user_id AND operation = :operation "
                "AND client_id = :client_id "
                "FOR UPDATE"
            ),
            {
                "user_id": owner_id,
                "operation": FEEDBACK_OPERATION,
                "client_id": client_id,
            },
        )
    ).first()
    if row is None:
        return None
    return FeedbackClaim(
        inserted=False,
        request_hash=str(row.request_hash),
        status=str(row.status),
        resource_id=row.resource_id,
        response_json=_as_object(row.response_json),
    )


async def insert_feedback(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    body: str,
) -> uuid.UUID:
    row = (
        await session.execute(
            text(
                "INSERT INTO public.feedback_submissions (user_id, client_id, body) "
                "VALUES (:user_id, :client_id, :body) "
                "RETURNING id"
            ),
            {"user_id": owner_id, "client_id": client_id, "body": body},
        )
    ).one()
    return row.id


async def complete_feedback(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    feedback_id: uuid.UUID,
) -> None:
    payload = json.dumps({"id": str(feedback_id)}, separators=(",", ":"))
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = :resource_type, "
            "resource_id = :resource_id, response_json = CAST(:payload AS jsonb), "
            "completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id AND status = 'in_progress'"
        ),
        {
            "user_id": owner_id,
            "operation": FEEDBACK_OPERATION,
            "client_id": client_id,
            "resource_type": FEEDBACK_RESOURCE_TYPE,
            "resource_id": feedback_id,
            "payload": payload,
        },
    )


async def reject_feedback(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    code: str,
) -> None:
    payload = json.dumps({"code": code}, separators=(",", ":"))
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = :resource_type, "
            "resource_id = NULL, response_json = CAST(:payload AS jsonb), "
            "completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id AND status = 'in_progress'"
        ),
        {
            "user_id": owner_id,
            "operation": FEEDBACK_OPERATION,
            "client_id": client_id,
            "resource_type": FEEDBACK_RESOURCE_TYPE,
            "payload": payload,
        },
    )


async def release_feedback_claim(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "DELETE FROM public.idempotency_records "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id AND status = 'in_progress'"
        ),
        {
            "user_id": owner_id,
            "operation": FEEDBACK_OPERATION,
            "client_id": client_id,
        },
    )
