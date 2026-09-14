"""Atomic daily_usage consume/reserve/release. Spec §§6.4, 15.2.

Insert-on-conflict then conditional UPDATE. Timezone hops reuse the still-open
usage_date. Caller must already be in a writable owner-claimed transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.quota import (
    QuotaSnapshot,
    coerce_timezone,
    daily_limit,
    local_usage_date,
    require_capability,
)

_ACTIVE_WINDOW_SQL = """
SELECT usage_date, timezone
FROM public.daily_usage
WHERE user_id = :user_id AND capability = :capability
  AND ((usage_date + 1)::timestamp AT TIME ZONE timezone) > :now
ORDER BY usage_date DESC
LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class QuotaReservation:
    id: uuid.UUID
    source_id: uuid.UUID
    capability: str
    amount: int
    usage_date: date
    timezone: str
    expires_at: datetime
    status: str
    snapshot: QuotaSnapshot


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("quota cannot write in a read-only transaction")


async def owner_timezone(session: AsyncSession, owner_id: uuid.UUID) -> str:
    value = await session.scalar(
        text("SELECT timezone FROM public.user_preferences WHERE user_id = :id"),
        {"id": owner_id},
    )
    return coerce_timezone(str(value) if value is not None else None)


async def resolve_usage_window(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    capability: str,
    now: datetime,
    timezone: str,
) -> tuple[date, str]:
    require_capability(capability)
    zone = coerce_timezone(timezone)
    row = (
        await session.execute(
            text(_ACTIVE_WINDOW_SQL),
            {"user_id": owner_id, "capability": capability, "now": now},
        )
    ).first()
    if row is not None:
        return row.usage_date, coerce_timezone(str(row.timezone))
    return local_usage_date(now, zone), zone


async def ensure_usage_row(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    capability: str,
    usage_date: date,
    timezone: str,
    limit_value: int,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.daily_usage ("
            "user_id, usage_date, timezone, capability, used, reserved, limit_value"
            ") VALUES ("
            ":user_id, :usage_date, :timezone, :capability, 0, 0, :limit_value"
            ") ON CONFLICT (user_id, usage_date, capability) DO NOTHING"
        ),
        {
            "user_id": owner_id,
            "usage_date": usage_date,
            "timezone": coerce_timezone(timezone),
            "capability": require_capability(capability),
            "limit_value": limit_value,
        },
    )


def _snapshot(row: object, *, capability: str) -> QuotaSnapshot:
    return QuotaSnapshot(
        capability=capability,
        used=int(getattr(row, "used")),
        reserved=int(getattr(row, "reserved")),
        limit=int(getattr(row, "limit_value")),
        usage_date=getattr(row, "usage_date"),
        timezone=coerce_timezone(str(getattr(row, "timezone"))),
        version=int(getattr(row, "version")),
    )


async def load_usage(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    capability: str,
    usage_date: date,
) -> QuotaSnapshot | None:
    row = (
        await session.execute(
            text(
                "SELECT used, reserved, limit_value, usage_date, timezone, version "
                "FROM public.daily_usage "
                "WHERE user_id = :user_id AND usage_date = :usage_date "
                "AND capability = :capability"
            ),
            {
                "user_id": owner_id,
                "usage_date": usage_date,
                "capability": require_capability(capability),
            },
        )
    ).first()
    if row is None:
        return None
    return _snapshot(row, capability=capability)


async def consume(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    capability: str,
    now: datetime,
    timezone: str,
    amount: int = 1,
    limit_value: int | None = None,
) -> QuotaSnapshot | None:
    cap = require_capability(capability)
    if amount < 1:
        raise ValueError("quota amount must be positive")
    limit = daily_limit(cap) if limit_value is None else limit_value
    usage_date, zone = await resolve_usage_window(
        session, owner_id=owner_id, capability=cap, now=now, timezone=timezone
    )
    await ensure_usage_row(
        session,
        owner_id=owner_id,
        capability=cap,
        usage_date=usage_date,
        timezone=zone,
        limit_value=limit,
    )
    row = (
        await session.execute(
            text(
                "UPDATE public.daily_usage "
                "SET used = used + :amount, version = version + 1, updated_at = :now "
                "WHERE user_id = :user_id AND usage_date = :usage_date "
                "AND capability = :capability "
                "AND used + reserved + :amount <= limit_value "
                "RETURNING used, reserved, limit_value, usage_date, timezone, version"
            ),
            {
                "user_id": owner_id,
                "usage_date": usage_date,
                "capability": cap,
                "amount": amount,
                "now": now,
            },
        )
    ).first()
    if row is None:
        return None
    return _snapshot(row, capability=cap)


async def apply_reserved_delta(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    capability: str,
    usage_date: date,
    amount: int,
    now: datetime,
    mode: str,
) -> QuotaSnapshot | None:
    cap = require_capability(capability)
    if amount < 1:
        raise ValueError("quota amount must be positive")
    if mode == "reserve":
        assignment = "reserved = reserved + :amount"
        predicate = "used + reserved + :amount <= limit_value"
    elif mode == "release":
        assignment = "reserved = reserved - :amount"
        predicate = "reserved >= :amount"
    elif mode == "commit":
        assignment = "reserved = reserved - :amount, used = used + :amount"
        predicate = "reserved >= :amount"
    else:
        raise ValueError("unsupported quota reservation mode")
    row = (
        await session.execute(
            text(
                "UPDATE public.daily_usage "
                f"SET {assignment}, version = version + 1, updated_at = :now "
                "WHERE user_id = :user_id AND usage_date = :usage_date "
                f"AND capability = :capability AND {predicate} "
                "RETURNING used, reserved, limit_value, usage_date, timezone, version"
            ),
            {
                "user_id": owner_id,
                "usage_date": usage_date,
                "capability": cap,
                "amount": amount,
                "now": now,
            },
        )
    ).first()
    if row is None:
        return None
    return _snapshot(row, capability=cap)


async def insert_reservation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    capability: str,
    usage_date: date,
    timezone: str,
    source_id: uuid.UUID,
    amount: int,
    expires_at: datetime,
) -> uuid.UUID | None:
    inserted = await session.scalar(
        text(
            "INSERT INTO private.quota_reservations ("
            "user_id, capability, usage_date, timezone, source_id, amount, expires_at"
            ") VALUES ("
            ":user_id, :capability, :usage_date, :timezone, :source_id, :amount, :expires_at"
            ") ON CONFLICT (user_id, capability, source_id) DO NOTHING "
            "RETURNING id"
        ),
        {
            "user_id": owner_id,
            "capability": require_capability(capability),
            "usage_date": usage_date,
            "timezone": coerce_timezone(timezone),
            "source_id": source_id,
            "amount": amount,
            "expires_at": expires_at,
        },
    )
    return uuid.UUID(str(inserted)) if inserted is not None else None


async def reopen_reservation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    capability: str,
    source_id: uuid.UUID,
    usage_date: date,
    timezone: str,
    amount: int,
    expires_at: datetime,
    now: datetime,
) -> uuid.UUID | None:
    reopened = await session.scalar(
        text(
            "UPDATE private.quota_reservations "
            "SET status = 'held', usage_date = :usage_date, timezone = :timezone, "
            "amount = :amount, expires_at = :expires_at, updated_at = :now "
            "WHERE user_id = :user_id AND capability = :capability "
            "AND source_id = :source_id AND status = 'released' "
            "RETURNING id"
        ),
        {
            "user_id": owner_id,
            "capability": require_capability(capability),
            "source_id": source_id,
            "usage_date": usage_date,
            "timezone": coerce_timezone(timezone),
            "amount": amount,
            "expires_at": expires_at,
            "now": now,
        },
    )
    return uuid.UUID(str(reopened)) if reopened is not None else None


async def load_reservation(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    capability: str,
    source_id: uuid.UUID,
) -> tuple[uuid.UUID, str, date, str, int, datetime] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, status, usage_date, timezone, amount, expires_at "
                "FROM private.quota_reservations "
                "WHERE user_id = :user_id AND capability = :capability "
                "AND source_id = :source_id"
            ),
            {
                "user_id": owner_id,
                "capability": require_capability(capability),
                "source_id": source_id,
            },
        )
    ).first()
    if row is None:
        return None
    return (
        uuid.UUID(str(row.id)),
        str(row.status),
        row.usage_date,
        coerce_timezone(str(row.timezone)),
        int(row.amount),
        row.expires_at,
    )


async def mark_reservation(
    session: AsyncSession,
    *,
    reservation_id: uuid.UUID,
    from_status: tuple[str, ...],
    to_status: str,
    now: datetime,
) -> bool:
    allowed = ", ".join(f"'{item}'" for item in from_status)
    marked = await session.scalar(
        text(
            "UPDATE private.quota_reservations SET status = :to_status, updated_at = :now "
            f"WHERE id = :id AND status IN ({allowed}) RETURNING id"
        ),
        {"id": reservation_id, "to_status": to_status, "now": now},
    )
    return marked is not None


async def claim_expired_reservations(
    session: AsyncSession, *, now: datetime, limit: int = 50
) -> tuple[tuple[uuid.UUID, uuid.UUID, str, date, int], ...]:
    rows = (
        await session.execute(
            text(
                "WITH picked AS ("
                "SELECT id FROM private.quota_reservations "
                "WHERE status IN ('held', 'expiring') AND expires_at <= :now "
                "ORDER BY expires_at ASC, id ASC "
                "FOR UPDATE SKIP LOCKED "
                "LIMIT :limit"
                ") UPDATE private.quota_reservations AS r "
                "SET status = 'expiring', updated_at = :now "
                "FROM picked WHERE r.id = picked.id "
                "RETURNING r.id, r.user_id, r.capability, r.usage_date, r.amount, r.status"
            ),
            {"now": now, "limit": limit},
        )
    ).all()
    return tuple(
        (
            uuid.UUID(str(row.id)),
            uuid.UUID(str(row.user_id)),
            str(row.capability),
            row.usage_date,
            int(row.amount),
        )
        for row in rows
        if str(getattr(row, "status", "expiring")) in {"held", "expiring"}
    )
