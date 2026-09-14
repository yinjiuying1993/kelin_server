"""Owner-filtered sight_uploads persistence. Spec §§8.4, 11.3."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.sight_upload import (
    ALLOWED_MIME,
    ORIGINAL_RETENTION,
    SIGHT_BUCKET,
    SIGHT_UPLOAD_OPERATION,
    UPLOAD_CLEANUP_AGGREGATE,
    UPLOAD_CLEANUP_EVENT,
    upload_cleanup_dedupe_key,
)
from app.repositories.feed import (
    IdempotencyClaim,
    claim_feed_idempotency,
    complete_feed_idempotency,
)
from app.schemas.social import OutboxPayload

_RETURNING = (
    "id, user_id, spirit_id, feed_id, client_id, bucket, object_path, "
    "expected_mime, expected_size, expected_sha256, status, expires_at, "
    "created_at, object_deleted_at"
)


@dataclass(frozen=True, slots=True)
class SightUploadRow:
    id: uuid.UUID
    user_id: uuid.UUID
    spirit_id: uuid.UUID
    feed_id: uuid.UUID
    client_id: uuid.UUID
    bucket: str
    object_path: str
    expected_mime: str
    expected_size: int
    expected_sha256: str
    status: str
    expires_at: datetime
    created_at: datetime
    object_deleted_at: datetime | None


async def claim_upload_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> IdempotencyClaim:
    return await claim_feed_idempotency(
        session,
        owner_id,
        client_id,
        request_hash,
        operation=SIGHT_UPLOAD_OPERATION,
    )


async def complete_upload_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    upload_id: uuid.UUID,
) -> None:
    await complete_feed_idempotency(
        session,
        owner_id,
        client_id,
        upload_id,
        operation=SIGHT_UPLOAD_OPERATION,
        resource_type="upload_session",
    )


async def fetch_by_id(
    session: AsyncSession, *, owner_id: uuid.UUID, upload_id: uuid.UUID
) -> SightUploadRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_RETURNING} FROM public.sight_uploads "
                "WHERE user_id = :user_id AND id = :id"
            ),
            {"user_id": owner_id, "id": upload_id},
        )
    ).first()
    if row is None:
        return None
    return _row(row)


async def fetch_by_client_id(
    session: AsyncSession, *, owner_id: uuid.UUID, client_id: uuid.UUID
) -> SightUploadRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_RETURNING} FROM public.sight_uploads "
                "WHERE user_id = :user_id AND client_id = :client_id"
            ),
            {"user_id": owner_id, "client_id": client_id},
        )
    ).first()
    if row is None:
        return None
    return _row(row)


async def lock_active_for_feed(
    session: AsyncSession, *, owner_id: uuid.UUID, feed_id: uuid.UUID
) -> tuple[SightUploadRow, ...]:
    rows = (
        await session.execute(
            text(
                f"SELECT {_RETURNING} FROM public.sight_uploads "
                "WHERE user_id = :user_id AND feed_id = :feed_id "
                "AND status IN ('issued', 'uploaded', 'verifying') FOR UPDATE"
            ),
            {"user_id": owner_id, "feed_id": feed_id},
        )
    ).all()
    return tuple(_row(row) for row in rows)


async def lock_owned_upload(
    session: AsyncSession, *, owner_id: uuid.UUID, upload_id: uuid.UUID
) -> SightUploadRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_RETURNING} FROM public.sight_uploads "
                "WHERE user_id = :user_id AND id = :id FOR UPDATE"
            ),
            {"user_id": owner_id, "id": upload_id},
        )
    ).first()
    if row is None:
        return None
    return _row(row)


async def list_due_cleanups(
    session: AsyncSession, *, owner_id: uuid.UUID, now: datetime
) -> tuple[SightUploadRow, ...]:
    sla_cutoff = now - ORIGINAL_RETENTION
    rows = (
        await session.execute(
            text(
                f"SELECT {_RETURNING} FROM public.sight_uploads "
                "WHERE user_id = :user_id AND object_deleted_at IS NULL AND ("
                "status IN ('consumed', 'rejected', 'expired') "
                "OR (status IN ('issued', 'uploaded', 'verifying') AND expires_at <= :now) "
                "OR created_at <= :sla_cutoff"
                ") ORDER BY created_at ASC, id ASC"
            ),
            {"user_id": owner_id, "now": now, "sla_cutoff": sla_cutoff},
        )
    ).all()
    return tuple(_row(row) for row in rows)


async def mark_object_deleted(
    session: AsyncSession, *, owner_id: uuid.UUID, upload_id: uuid.UUID, now: datetime
) -> SightUploadRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.sight_uploads SET "
                "object_deleted_at = COALESCE(object_deleted_at, :now), "
                "status = CASE WHEN status IN ('issued', 'uploaded', 'verifying') "
                "THEN 'expired' ELSE status END "
                "WHERE user_id = :user_id AND id = :id "
                f"RETURNING {_RETURNING}"
            ),
            {"now": now, "user_id": owner_id, "id": upload_id},
        )
    ).first()
    if row is None:
        return None
    return _row(row)


async def insert_cleanup_outbox(
    session: AsyncSession, *, owner_id: uuid.UUID, upload_id: uuid.UUID
) -> bool:
    payload = OutboxPayload(resource_id=upload_id).model_dump(mode="json", exclude_none=True)
    params = {
        "aggregate_type": UPLOAD_CLEANUP_AGGREGATE,
        "aggregate_id": upload_id,
        "event_type": UPLOAD_CLEANUP_EVENT,
        "dedupe_key": upload_cleanup_dedupe_key(upload_id),
        "owner_id": owner_id,
        "payload": json.dumps(payload),
    }
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO public.outbox_events ("
                    "aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload"
                    ") VALUES ("
                    ":aggregate_type, :aggregate_id, :event_type, :dedupe_key, :owner_id, "
                    "CAST(:payload AS jsonb)"
                    ")"
                ),
                params,
            )
    except IntegrityError as exc:
        blob = str(exc).lower()
        if "23505" in blob or "duplicate key" in blob or "uq_outbox_events_dedupe_key" in blob:
            return False
        raise
    return True


async def expire_issued_for_feed(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    feed_id: uuid.UUID,
    except_client_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.sight_uploads SET status = 'expired' "
            "WHERE user_id = :user_id AND feed_id = :feed_id AND status = 'issued' "
            "AND client_id <> :except_client_id"
        ),
        {
            "user_id": owner_id,
            "feed_id": feed_id,
            "except_client_id": except_client_id,
        },
    )


async def mark_expired(
    session: AsyncSession, *, owner_id: uuid.UUID, upload_id: uuid.UUID
) -> SightUploadRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.sight_uploads SET status = 'expired' "
                "WHERE user_id = :user_id AND id = :id "
                "AND status IN ('issued', 'uploaded', 'verifying') "
                f"RETURNING {_RETURNING}"
            ),
            {"user_id": owner_id, "id": upload_id},
        )
    ).first()
    if row is None:
        existing = await fetch_by_id(session, owner_id=owner_id, upload_id=upload_id)
        return existing
    return _row(row)


async def mark_verifying(
    session: AsyncSession, *, owner_id: uuid.UUID, upload_id: uuid.UUID
) -> SightUploadRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.sight_uploads SET status = 'verifying' "
                "WHERE user_id = :user_id AND id = :id "
                "AND status IN ('issued', 'uploaded', 'verifying') "
                f"RETURNING {_RETURNING}"
            ),
            {"user_id": owner_id, "id": upload_id},
        )
    ).first()
    if row is None:
        return None
    return _row(row)


async def mark_terminal(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    upload_id: uuid.UUID,
    status: str,
    now: datetime,
    object_deleted: bool,
) -> SightUploadRow | None:
    if status not in {"consumed", "rejected"}:
        raise ValueError("upload terminal status must be consumed or rejected")
    deleted_sql = (
        ", object_deleted_at = COALESCE(object_deleted_at, :now) " if object_deleted else ""
    )
    row = (
        await session.execute(
            text(
                "UPDATE public.sight_uploads SET status = :status "
                f"{deleted_sql}"
                "WHERE user_id = :user_id AND id = :id "
                f"RETURNING {_RETURNING}"
            ),
            {"user_id": owner_id, "id": upload_id, "status": status, "now": now},
        )
    ).first()
    if row is None:
        return None
    return _row(row)


async def insert_issued(
    session: AsyncSession,
    *,
    upload_id: uuid.UUID,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    feed_id: uuid.UUID,
    client_id: uuid.UUID,
    object_path: str,
    size_bytes: int,
    sha256: str,
    expires_at: datetime,
    created_at: datetime,
) -> SightUploadRow:
    row = (
        await session.execute(
            text(
                "INSERT INTO public.sight_uploads ("
                "id, user_id, spirit_id, feed_id, client_id, bucket, object_path, "
                "expected_mime, expected_size, expected_sha256, status, expires_at, "
                "created_at"
                ") VALUES ("
                ":id, :user_id, :spirit_id, :feed_id, :client_id, :bucket, :object_path, "
                ":expected_mime, :expected_size, :expected_sha256, 'issued', :expires_at, "
                ":created_at"
                f") RETURNING {_RETURNING}"
            ),
            {
                "id": upload_id,
                "user_id": owner_id,
                "spirit_id": spirit_id,
                "feed_id": feed_id,
                "client_id": client_id,
                "bucket": SIGHT_BUCKET,
                "object_path": object_path,
                "expected_mime": ALLOWED_MIME,
                "expected_size": size_bytes,
                "expected_sha256": sha256,
                "expires_at": expires_at,
                "created_at": created_at,
            },
        )
    ).one()
    return _row(row)


def _row(row: Any) -> SightUploadRow:
    return SightUploadRow(
        id=row.id,
        user_id=row.user_id,
        spirit_id=row.spirit_id,
        feed_id=row.feed_id,
        client_id=row.client_id,
        bucket=str(row.bucket),
        object_path=str(row.object_path),
        expected_mime=str(row.expected_mime),
        expected_size=int(row.expected_size),
        expected_sha256=str(row.expected_sha256),
        status=str(row.status),
        expires_at=row.expires_at,
        created_at=row.created_at,
        object_deleted_at=row.object_deleted_at,
    )
