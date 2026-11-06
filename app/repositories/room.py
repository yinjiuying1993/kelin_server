"""Persist an already assembled room four-piece. Does not recompute sources."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def upsert_projection(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    stage_image_key: str,
    primary_emotion_label: str,
    letter_key: str | None,
    text_status: str,
    status: str,
    snapshot_version: int,
) -> None:
    await session.execute(
        text(
            "INSERT INTO public.room_projections ("
            "spirit_id, stage_image_key, primary_emotion_label, letter_key, "
            "text_status, status, snapshot_version"
            ") VALUES ("
            ":spirit_id, :stage_image_key, :primary_emotion_label, :letter_key, "
            ":text_status, :status, :snapshot_version"
            ") ON CONFLICT (spirit_id) DO UPDATE SET "
            "stage_image_key = EXCLUDED.stage_image_key, "
            "primary_emotion_label = EXCLUDED.primary_emotion_label, "
            "letter_key = EXCLUDED.letter_key, "
            "text_status = EXCLUDED.text_status, "
            "status = EXCLUDED.status, "
            "snapshot_version = EXCLUDED.snapshot_version"
        ),
        {
            "spirit_id": spirit_id,
            "stage_image_key": stage_image_key,
            "primary_emotion_label": primary_emotion_label,
            "letter_key": letter_key,
            "text_status": text_status,
            "status": status,
            "snapshot_version": snapshot_version,
        },
    )
