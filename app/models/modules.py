"""V1 module tables split by write ownership. S2-T01."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text as sql_text

from app.db.base import Base

_STAGE_IMAGE = "stage_image_key IN ('whelp_image', 'formed_image', 'awake_image')"
_STATUS = "status IN ('home', 'away')"


class SpiritAwareness(Base):
    __tablename__ = "spirit_awareness"
    __table_args__ = (
        CheckConstraint(_STATUS, name="ck_spirit_awareness_status"),
        {"schema": "public"},
    )

    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'home'"))
    last_interact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    study_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    away_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    visit_cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    has_been_lost: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    last_state_settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class ChatOverlay(Base):
    __tablename__ = "chat_overlays"
    __table_args__ = (
        CheckConstraint("overlay_version >= 1", name="ck_chat_overlays_version"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_chat_overlays_payload_object"),
        {"schema": "public"},
    )

    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        primary_key=True,
    )
    overlay_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=sql_text("1")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class RoomProjection(Base):
    __tablename__ = "room_projections"
    __table_args__ = (
        CheckConstraint(_STAGE_IMAGE, name="ck_room_projections_stage_image_key"),
        CheckConstraint(_STATUS, name="ck_room_projections_status"),
        CheckConstraint("snapshot_version >= 1", name="ck_room_projections_snapshot_version"),
        CheckConstraint(
            "primary_emotion_label IS NULL OR char_length(primary_emotion_label) BETWEEN 1 AND 32",
            name="ck_room_projections_primary_label",
        ),
        {"schema": "public"},
    )

    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        primary_key=True,
    )
    stage_image_key: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'whelp_image'")
    )
    primary_emotion_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    letter_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'home'"))
    snapshot_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=sql_text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class ActionEvent(Base):
    __tablename__ = "action_events"
    __table_args__ = (
        UniqueConstraint("spirit_id", "event_id", name="uq_action_events_spirit_event_id"),
        UniqueConstraint("spirit_id", "client_id", name="uq_action_events_spirit_client_id"),
        CheckConstraint(
            "status IN ('accepted', 'rejected', 'conflict')",
            name="ck_action_events_status",
        ),
        CheckConstraint("char_length(kind) BETWEEN 1 AND 64", name="ck_action_events_kind"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_action_events_payload_object"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class ActionQuota(Base):
    __tablename__ = "action_quotas"
    __table_args__ = (
        CheckConstraint(
            "capability IN ('chat', 'asr', 'tts')",
            name="ck_action_quotas_capability",
        ),
        CheckConstraint("used >= 0", name="ck_action_quotas_used"),
        CheckConstraint("reserved >= 0", name="ck_action_quotas_reserved"),
        CheckConstraint("limit_value >= 0", name="ck_action_quotas_limit_value"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    capability: Mapped[str] = mapped_column(Text, primary_key=True)
    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    used: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sql_text("0"))
    reserved: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sql_text("0"))
    limit_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sql_text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
