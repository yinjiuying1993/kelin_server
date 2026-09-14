"""Account deletion persistence. Spec §§6.5, 14.9. API uses definers; worker loads claimed jobs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class DeletionAccept:
    deletion_id: uuid.UUID | None
    requested_at: datetime | None
    outcome: str


@dataclass(frozen=True, slots=True)
class DeletionRow:
    id: uuid.UUID
    owner_id: uuid.UUID
    client_id: uuid.UUID
    status: str
    requested_at: datetime
    storage_cursor: str | None
    attempts: int
    last_error_code: str | None
    owner_hash: str | None
    completed_at: datetime | None
    dead_at: datetime | None


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("account deletion cannot write in a read-only transaction")


async def accept_account_deletion(
    session: AsyncSession,
    *,
    client_id: uuid.UUID,
    owner_hash: str,
) -> DeletionAccept:
    row = (
        await session.execute(
            text(
                "SELECT deletion_id, requested_at, outcome "
                "FROM private.mark_account_deleting(:client_id, :owner_hash)"
            ),
            {"client_id": client_id, "owner_hash": owner_hash},
        )
    ).first()
    if row is None:
        return DeletionAccept(deletion_id=None, requested_at=None, outcome="pending")
    return DeletionAccept(
        deletion_id=row.deletion_id,
        requested_at=row.requested_at,
        outcome=str(row.outcome),
    )


async def load_claimed(
    session: AsyncSession, deletion_id: uuid.UUID
) -> DeletionRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, owner_id, client_id, status, requested_at, storage_cursor, "
                "attempts, last_error_code, owner_hash, completed_at, dead_at "
                "FROM public.account_deletions WHERE id = :id FOR UPDATE"
            ),
            {"id": deletion_id},
        )
    ).first()
    if row is None:
        return None
    return DeletionRow(
        id=row.id,
        owner_id=row.owner_id,
        client_id=row.client_id,
        status=str(row.status),
        requested_at=row.requested_at,
        storage_cursor=row.storage_cursor,
        attempts=int(row.attempts),
        last_error_code=row.last_error_code,
        owner_hash=row.owner_hash,
        completed_at=row.completed_at,
        dead_at=row.dead_at,
    )


async def save_progress(
    session: AsyncSession,
    *,
    deletion_id: uuid.UUID,
    status: str,
    storage_cursor: str | None,
    attempts: int,
    last_error_code: str | None,
    completed_at: datetime | None = None,
    dead_at: datetime | None = None,
) -> None:
    await session.execute(
        text(
            "UPDATE public.account_deletions SET "
            "status = :status, storage_cursor = :storage_cursor, attempts = :attempts, "
            "last_error_code = :last_error_code, completed_at = :completed_at, "
            "dead_at = :dead_at "
            "WHERE id = :id"
        ),
        {
            "id": deletion_id,
            "status": status,
            "storage_cursor": storage_cursor,
            "attempts": attempts,
            "last_error_code": last_error_code,
            "completed_at": completed_at,
            "dead_at": dead_at,
        },
    )


async def residue_exists(session: AsyncSession, owner_id: uuid.UUID) -> bool:
    found = await session.scalar(
        text("SELECT private.account_residue_exists(:owner_id)"),
        {"owner_id": owner_id},
    )
    return bool(found)
