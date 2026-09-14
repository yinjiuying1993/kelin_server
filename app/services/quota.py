"""Owner-claimed daily quota. Spec §§6.4, 8.10, 15.2.

Consume is a single conditional UPDATE. Provider calls use reserve/commit/release
with an injected clock. GET and timezone hops never mint extra quota.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.quota import (
    RESERVATION_TTL,
    QuotaSnapshot,
    daily_limit,
    exceeded_details,
    quota_usage,
    require_capability,
)
from app.domain.spirit_state import require_aware
from app.repositories import quota as quota_repo
from app.schemas.spirit import QuotaUsage

_WORKER_ROLE = "kelin_worker"


def _exceeded(snapshot: QuotaSnapshot) -> ApiError:
    return ApiError(
        "QUOTA_EXCEEDED",
        public_error_message("QUOTA_EXCEEDED"),
        status_code=429,
        retryable=False,
        details=exceeded_details(snapshot),
    )


async def consume(
    session: AsyncSession,
    owner_id: uuid.UUID,
    capability: str,
    *,
    now: datetime,
    timezone: str | None = None,
    amount: int = 1,
    limit_value: int | None = None,
) -> QuotaSnapshot:
    require_aware(now, field="now")
    cap = require_capability(capability)
    await quota_repo.assert_writable_transaction(session)
    zone = timezone or await quota_repo.owner_timezone(session, owner_id)
    snapshot = await quota_repo.consume(
        session,
        owner_id=owner_id,
        capability=cap,
        now=now,
        timezone=zone,
        amount=amount,
        limit_value=limit_value,
    )
    if snapshot is not None:
        return snapshot
    usage_date, used_zone = await quota_repo.resolve_usage_window(
        session, owner_id=owner_id, capability=cap, now=now, timezone=zone
    )
    current = await quota_repo.load_usage(
        session, owner_id=owner_id, capability=cap, usage_date=usage_date
    )
    if current is None:
        limit = daily_limit(cap) if limit_value is None else limit_value
        current = QuotaSnapshot(
            capability=cap,
            used=0,
            reserved=0,
            limit=limit,
            usage_date=usage_date,
            timezone=used_zone,
            version=1,
        )
    raise _exceeded(current)


async def reserve(
    session: AsyncSession,
    owner_id: uuid.UUID,
    capability: str,
    *,
    source_id: uuid.UUID,
    now: datetime,
    timezone: str | None = None,
    amount: int = 1,
    limit_value: int | None = None,
) -> quota_repo.QuotaReservation:
    require_aware(now, field="now")
    cap = require_capability(capability)
    await quota_repo.assert_writable_transaction(session)
    zone = timezone or await quota_repo.owner_timezone(session, owner_id)
    existing = await quota_repo.load_reservation(
        session, owner_id=owner_id, capability=cap, source_id=source_id
    )
    if existing is not None:
        reservation_id, status, usage_date, used_zone, held_amount, expires_at = existing
        snapshot = await quota_repo.load_usage(
            session, owner_id=owner_id, capability=cap, usage_date=usage_date
        )
        if snapshot is None:
            raise RuntimeError("quota reservation is missing its usage row")
        if status == "committed":
            return quota_repo.QuotaReservation(
                id=reservation_id,
                source_id=source_id,
                capability=cap,
                amount=held_amount,
                usage_date=usage_date,
                timezone=used_zone,
                expires_at=expires_at,
                status=status,
                snapshot=snapshot,
            )
        if status == "held" and expires_at > now:
            return quota_repo.QuotaReservation(
                id=reservation_id,
                source_id=source_id,
                capability=cap,
                amount=held_amount,
                usage_date=usage_date,
                timezone=used_zone,
                expires_at=expires_at,
                status=status,
                snapshot=snapshot,
            )
        if status in {"held", "expiring"}:
            released = await quota_repo.apply_reserved_delta(
                session,
                owner_id=owner_id,
                capability=cap,
                usage_date=usage_date,
                amount=held_amount,
                now=now,
                mode="release",
            )
            await quota_repo.mark_reservation(
                session,
                reservation_id=reservation_id,
                from_status=(status,),
                to_status="released",
                now=now,
            )
            if released is None and snapshot.reserved < held_amount:
                pass
    usage_date, used_zone = await quota_repo.resolve_usage_window(
        session, owner_id=owner_id, capability=cap, now=now, timezone=zone
    )
    limit = daily_limit(cap) if limit_value is None else limit_value
    await quota_repo.ensure_usage_row(
        session,
        owner_id=owner_id,
        capability=cap,
        usage_date=usage_date,
        timezone=used_zone,
        limit_value=limit,
    )
    expires_at = now + RESERVATION_TTL
    created_id = await quota_repo.insert_reservation(
        session,
        owner_id=owner_id,
        capability=cap,
        usage_date=usage_date,
        timezone=used_zone,
        source_id=source_id,
        amount=amount,
        expires_at=expires_at,
    )
    if created_id is None:
        created_id = await quota_repo.reopen_reservation(
            session,
            owner_id=owner_id,
            capability=cap,
            source_id=source_id,
            usage_date=usage_date,
            timezone=used_zone,
            amount=amount,
            expires_at=expires_at,
            now=now,
        )
    if created_id is None:
        existing = await quota_repo.load_reservation(
            session, owner_id=owner_id, capability=cap, source_id=source_id
        )
        if existing is not None and existing[1] in {"held", "committed"}:
            existing_id, status, usage_date, used_zone, held_amount, expires_at = existing
            snapshot = await quota_repo.load_usage(
                session, owner_id=owner_id, capability=cap, usage_date=usage_date
            )
            if snapshot is None:
                raise RuntimeError("quota reservation is missing its usage row")
            return quota_repo.QuotaReservation(
                id=existing_id,
                source_id=source_id,
                capability=cap,
                amount=held_amount,
                usage_date=usage_date,
                timezone=used_zone,
                expires_at=expires_at,
                status=status,
                snapshot=snapshot,
            )
        raise RuntimeError("quota reservation could not be created")
    snapshot = await quota_repo.apply_reserved_delta(
        session,
        owner_id=owner_id,
        capability=cap,
        usage_date=usage_date,
        amount=amount,
        now=now,
        mode="reserve",
    )
    if snapshot is None:
        await quota_repo.mark_reservation(
            session,
            reservation_id=created_id,
            from_status=("held",),
            to_status="released",
            now=now,
        )
        current = await quota_repo.load_usage(
            session, owner_id=owner_id, capability=cap, usage_date=usage_date
        )
        if current is None:
            current = QuotaSnapshot(
                capability=cap,
                used=0,
                reserved=0,
                limit=limit,
                usage_date=usage_date,
                timezone=used_zone,
                version=1,
            )
        raise _exceeded(current)
    return quota_repo.QuotaReservation(
        id=created_id,
        source_id=source_id,
        capability=cap,
        amount=amount,
        usage_date=usage_date,
        timezone=used_zone,
        expires_at=expires_at,
        status="held",
        snapshot=snapshot,
    )


async def commit(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    reservation: quota_repo.QuotaReservation,
    now: datetime,
) -> QuotaSnapshot:
    require_aware(now, field="now")
    await quota_repo.assert_writable_transaction(session)
    if reservation.status == "committed":
        return reservation.snapshot
    snapshot = await quota_repo.apply_reserved_delta(
        session,
        owner_id=owner_id,
        capability=reservation.capability,
        usage_date=reservation.usage_date,
        amount=reservation.amount,
        now=now,
        mode="commit",
    )
    if snapshot is None:
        raise RuntimeError("quota reservation commit matched no reserved amount")
    await quota_repo.mark_reservation(
        session,
        reservation_id=reservation.id,
        from_status=("held", "expiring"),
        to_status="committed",
        now=now,
    )
    return snapshot


async def release(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    reservation: quota_repo.QuotaReservation,
    now: datetime,
) -> QuotaSnapshot | None:
    require_aware(now, field="now")
    await quota_repo.assert_writable_transaction(session)
    if reservation.status in {"committed", "released"}:
        return reservation.snapshot
    snapshot = await quota_repo.apply_reserved_delta(
        session,
        owner_id=owner_id,
        capability=reservation.capability,
        usage_date=reservation.usage_date,
        amount=reservation.amount,
        now=now,
        mode="release",
    )
    await quota_repo.mark_reservation(
        session,
        reservation_id=reservation.id,
        from_status=("held", "expiring"),
        to_status="released",
        now=now,
    )
    return snapshot


@asynccontextmanager
async def hold(
    session: AsyncSession,
    owner_id: uuid.UUID,
    capability: str,
    *,
    source_id: uuid.UUID,
    now: datetime,
    timezone: str | None = None,
    amount: int = 1,
    limit_value: int | None = None,
) -> AsyncIterator[quota_repo.QuotaReservation]:
    reservation = await reserve(
        session,
        owner_id,
        capability,
        source_id=source_id,
        now=now,
        timezone=timezone,
        amount=amount,
        limit_value=limit_value,
    )
    try:
        yield reservation
    except BaseException:
        if reservation.status == "held":
            await release(session, owner_id, reservation=reservation, now=now)
        raise
    else:
        if reservation.status == "held":
            await commit(session, owner_id, reservation=reservation, now=now)


def usage_patch(snapshot: QuotaSnapshot) -> tuple[QuotaUsage, ...]:
    return (quota_usage(snapshot),)


async def expire_due_reservations(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int = 50,
) -> int:
    require_aware(now, field="now")
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        claimed = await quota_repo.claim_expired_reservations(session, now=now, limit=limit)
    released = 0
    for reservation_id, owner_id, capability, usage_date, amount in claimed:
        user = CurrentUser(id=owner_id)
        async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
            await quota_repo.apply_reserved_delta(
                session,
                owner_id=owner_id,
                capability=capability,
                usage_date=usage_date,
                amount=amount,
                now=now,
                mode="release",
            )
            await quota_repo.mark_reservation(
                session,
                reservation_id=reservation_id,
                from_status=("expiring", "held"),
                to_status="released",
                now=now,
            )
            released += 1
    return released
