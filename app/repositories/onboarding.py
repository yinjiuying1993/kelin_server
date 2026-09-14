"""Owner-filtered onboarding complete persistence. Spec §§8.2, 9.4."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.onboarding import ONBOARDING_COMPLETE_OPERATION


@dataclass(frozen=True, slots=True)
class CompleteIdempotencyClaim:
    inserted: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class HatchProjection:
    ordinary_dialogue_rounds: int
    updated_at: datetime


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("onboarding complete cannot write in a read-only transaction")


async def count_complete_onboarding_rounds(session: AsyncSession, spirit_id: uuid.UUID) -> int:
    value = await session.scalar(
        text(
            "SELECT count(*) FROM public.messages u "
            "WHERE u.spirit_id = :spirit_id AND u.role = 'user' "
            "AND u.onboarding = true AND u.status = 'accepted' "
            "AND EXISTS ("
            "SELECT 1 FROM public.messages s "
            "WHERE s.spirit_id = u.spirit_id AND s.reply_to_message_id = u.id "
            "AND s.role = 'spirit' AND s.status = 'generated' AND s.onboarding = true"
            ")"
        ),
        {"spirit_id": spirit_id},
    )
    return int(value or 0)


async def claim_complete_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> CompleteIdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": ONBOARDING_COMPLETE_OPERATION,
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
                    "RETURNING request_hash, status, resource_id"
                ),
                params,
            )
        ).first()
        if inserted is not None:
            return CompleteIdempotencyClaim(
                inserted=True,
                request_hash=str(inserted.request_hash),
                status=str(inserted.status),
                resource_id=inserted.resource_id,
            )
        existing = (
            await session.execute(
                text(
                    "SELECT request_hash, status, resource_id "
                    "FROM public.idempotency_records "
                    "WHERE user_id = :user_id AND operation = :operation "
                    "AND client_id = :client_id "
                    "FOR UPDATE"
                ),
                params,
            )
        ).first()
        if existing is not None:
            return CompleteIdempotencyClaim(
                inserted=False,
                request_hash=str(existing.request_hash),
                status=str(existing.status),
                resource_id=existing.resource_id,
            )
    raise RuntimeError("onboarding complete idempotency claim failed")


async def fetch_complete_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
) -> CompleteIdempotencyClaim | None:
    row = (
        await session.execute(
            text(
                "SELECT request_hash, status, resource_id "
                "FROM public.idempotency_records "
                "WHERE user_id = :user_id AND operation = :operation "
                "AND client_id = :client_id "
                "FOR UPDATE"
            ),
            {
                "user_id": owner_id,
                "operation": ONBOARDING_COMPLETE_OPERATION,
                "client_id": client_id,
            },
        )
    ).first()
    if row is None:
        return None
    return CompleteIdempotencyClaim(
        inserted=False,
        request_hash=str(row.request_hash),
        status=str(row.status),
        resource_id=row.resource_id,
    )


async def complete_complete_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    spirit_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = 'spirit', "
            "resource_id = :resource_id, completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": ONBOARDING_COMPLETE_OPERATION,
            "client_id": client_id,
            "resource_id": spirit_id,
        },
    )


async def apply_hatch(
    session: AsyncSession,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    *,
    now: datetime,
) -> datetime | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.spirits SET "
                "hatched_at = :now, onboarding_completed_at = :now, "
                "version = version + 1 "
                "WHERE id = :id AND user_id = :user_id "
                "AND hatched_at IS NULL AND onboarding_completed_at IS NULL "
                "AND onboarding_step = 5 "
                "RETURNING hatched_at"
            ),
            {"now": now, "id": spirit_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return now


async def fetch_hatch_projection(
    session: AsyncSession, owner_id: uuid.UUID
) -> HatchProjection | None:
    row = (
        await session.execute(
            text(
                "SELECT ordinary_dialogue_rounds, updated_at "
                "FROM public.spirits WHERE user_id = :user_id"
            ),
            {"user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return HatchProjection(
        ordinary_dialogue_rounds=int(row.ordinary_dialogue_rounds),
        updated_at=row.updated_at,
    )
