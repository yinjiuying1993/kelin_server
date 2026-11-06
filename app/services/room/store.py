"""Write the current projection row from an assembled four-piece."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import room as room_repo
from app.schemas.room import RoomPublic


async def save_projection(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    room: RoomPublic,
    snapshot_version: int,
) -> None:
    await room_repo.upsert_projection(
        session,
        spirit_id=spirit_id,
        stage_image_key=room.stage_image_key,
        primary_emotion_label="平静",
        letter_key=room.letter_key,
        text_status=room.text_status,
        status=room.status,
        snapshot_version=snapshot_version,
    )


async def persist_assembled(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    now: datetime,
) -> None:
    """Read the settled aggregate and store one homepage four-piece."""
    from app.repositories import bootstrap as bootstrap_repo
    from app.services.bootstrap import room_public_from_aggregate

    rows = await bootstrap_repo.load_bootstrap_aggregate_rows(
        session, owner_id, now=now, require_read_snapshot=False
    )
    if rows.spirit is None:
        return
    await save_projection(
        session,
        spirit_id=rows.spirit.id,
        room=room_public_from_aggregate(rows, now=now),
        snapshot_version=rows.spirit.snapshot_version,
    )
