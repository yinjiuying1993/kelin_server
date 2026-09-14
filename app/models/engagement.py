from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    desc,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text as sql_text

from app.db.base import Base


class ConversationWindow(Base):
    __tablename__ = "conversation_windows"
    __table_args__ = (
        UniqueConstraint(
            "spirit_id",
            "start_message_id",
            "end_message_id",
            name="uq_conversation_windows_spirit_start_end",
        ),
        CheckConstraint(
            "status IN ('open', 'ready', 'extracting', 'extracted', 'failed')",
            name="ck_conversation_windows_status",
        ),
        CheckConstraint(
            "extract_attempts >= 0",
            name="ck_conversation_windows_extract_attempts",
        ),
        Index(
            "uq_conversation_windows_spirit_extract_client_id",
            "spirit_id",
            "extract_client_id",
            unique=True,
            postgresql_where=sql_text("extract_client_id IS NOT NULL"),
        ),
        Index(
            "windows_ready",
            "status",
            "created_at",
            postgresql_where=sql_text("status IN ('ready', 'failed')"),
        ),
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
    start_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "messages.id",
            deferrable=True,
            initially="DEFERRED",
            name="fk_conversation_windows_start_message_id",
        ),
        nullable=False,
    )
    end_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "messages.id",
            deferrable=True,
            initially="DEFERRED",
            name="fk_conversation_windows_end_message_id",
        ),
        nullable=False,
    )
    user_round_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    onboarding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'open'"))
    extract_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    extract_client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'spirit', 'system')", name="ck_messages_role"),
        CheckConstraint(
            "char_length(content) BETWEEN 1 AND 4000",
            name="ck_messages_content_length",
        ),
        CheckConstraint("source IN ('text', 'voice', 'onboarding')", name="ck_messages_source"),
        CheckConstraint(
            "status IN ('accepted', 'generated', 'failed')",
            name="ck_messages_status",
        ),
        CheckConstraint(
            "(role = 'user' AND client_id IS NOT NULL) "
            "OR (role IN ('spirit', 'system') AND client_id IS NULL)",
            name="ck_messages_user_client_id",
        ),
        CheckConstraint(
            "jsonb_typeof(source_refs) = 'array'",
            name="ck_messages_source_refs_array",
        ),
        Index(
            "uq_messages_spirit_id_client_id",
            "spirit_id",
            "client_id",
            unique=True,
            postgresql_where=sql_text("client_id IS NOT NULL"),
        ),
        Index("messages_page", "spirit_id", desc("created_at"), desc("id")),
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
    conversation_window_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "conversation_windows.id",
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=True,
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    onboarding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_refs: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb"),
    )
    provider_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class Feed(Base):
    __tablename__ = "feeds"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_feeds_user_id_client_id"),
        CheckConstraint(
            "kind IN ('food', 'sight', 'knowledge', 'emotion', 'promise')",
            name="ck_feeds_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'accepted', 'rejected', 'cancelled')",
            name="ck_feeds_status",
        ),
        CheckConstraint(
            "(kind <> 'promise' AND promise_status IS NULL) "
            "OR (kind = 'promise' AND promise_status IN ('active', 'completed', 'cancelled'))",
            name="ck_feeds_promise_status",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_feeds_payload_object"),
        Index(
            "uq_feeds_spirit_one_active_promise",
            "spirit_id",
            unique=True,
            postgresql_where=sql_text("kind = 'promise' AND promise_status = 'active'"),
        ),
        Index(
            "feeds_pending",
            "status",
            "updated_at",
            postgresql_where=sql_text("status IN ('pending', 'processing')"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'pending'"))
    rejection_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    effect_applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    promise_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sql_text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "type IN ('preference', 'knowledge', 'emotion', 'relation', 'speech', 'sight')",
            name="ck_memories_type",
        ),
        CheckConstraint(
            "char_length(summary) BETWEEN 1 AND 500",
            name="ck_memories_summary_length",
        ),
        CheckConstraint("salience BETWEEN 0 AND 100", name="ck_memories_salience"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memories_confidence"),
        CheckConstraint(
            "(status = 'active' AND sealed_at IS NULL AND deleted_at IS NULL) "
            "OR (status = 'sealed' AND sealed_at IS NOT NULL AND deleted_at IS NULL) "
            "OR (status = 'deleted' AND deleted_at IS NOT NULL)",
            name="ck_memories_status",
        ),
        Index(
            "memories_active_page",
            "spirit_id",
            desc("created_at"),
            desc("id"),
            postgresql_where=sql_text("status = 'active'"),
        ),
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
    type: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
    )
    salience: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'active'"))
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_feed_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feeds.id", ondelete="SET NULL"),
        nullable=True,
    )
    corrected_from_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="SET NULL"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sql_text("1"))
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class StyleSample(Base):
    __tablename__ = "style_samples"
    __table_args__ = (
        UniqueConstraint(
            "spirit_id",
            "kind",
            "text",
            name="uq_style_samples_spirit_kind_text",
        ),
        CheckConstraint(
            "kind IN ('user_dialect', 'user_filler', 'spirit_catchphrase')",
            name="ck_style_samples_kind",
        ),
        CheckConstraint(
            "char_length(text) BETWEEN 1 AND 100",
            name="ck_style_samples_text_length",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_style_samples_status"),
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
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sql_text("1"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'active'"))
    source_window_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversation_windows.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class SightUpload(Base):
    __tablename__ = "sight_uploads"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_sight_uploads_user_id_client_id"),
        UniqueConstraint("bucket", "object_path", name="uq_sight_uploads_bucket_object_path"),
        CheckConstraint(
            "status IN ('issued', 'uploaded', 'verifying', 'consumed', 'expired', 'rejected')",
            name="ck_sight_uploads_status",
        ),
        CheckConstraint("expected_mime = 'image/jpeg'", name="ck_sight_uploads_expected_mime"),
        CheckConstraint(
            "expected_size BETWEEN 1 AND 5242880",
            name="ck_sight_uploads_expected_size",
        ),
        CheckConstraint(
            "expected_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_sight_uploads_expected_sha256",
        ),
        Index(
            "uploads_expire",
            "status",
            "expires_at",
            postgresql_where=sql_text("status IN ('issued', 'uploaded', 'verifying')"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    feed_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("feeds.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    bucket: Mapped[str] = mapped_column(Text, nullable=False)
    object_path: Mapped[str] = mapped_column(Text, nullable=False)
    expected_mime: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'image/jpeg'")
    )
    expected_size: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'issued'"))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("(now() + interval '10 minutes')"),
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    object_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class GrowthEvent(Base):
    __tablename__ = "growth_events"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "event_type",
            name="uq_growth_events_source_type_source_id_event_type",
        ),
        CheckConstraint(
            "event_type IN ("
            "'chat_completed', 'feed_accepted', 'promise_completed', "
            "'pact_answered', 'pact_completed', 'memory_added', "
            "'visit_completed', 'returned_from_lost', 'time_passed'"
            ")",
            name="ck_growth_events_event_type",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_growth_events_payload_object"),
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
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class DailyUsage(Base):
    __tablename__ = "daily_usage"
    __table_args__ = (
        CheckConstraint(
            "capability IN ("
            "'chat', 'asr', 'tts', 'sight', 'search', 'food', "
            "'knowledge', 'emotion', 'visit', 'pact'"
            ")",
            name="ck_daily_usage_capability",
        ),
        CheckConstraint("used >= 0", name="ck_daily_usage_used"),
        CheckConstraint("reserved >= 0", name="ck_daily_usage_reserved"),
        CheckConstraint("limit_value >= 0", name="ck_daily_usage_limit_value"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    capability: Mapped[str] = mapped_column(Text, primary_key=True)
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


class AiUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = ({"schema": "public"},)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    model_alias: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_units: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    audio_seconds: Mapped[Decimal | None] = mapped_column(Numeric(8, 3), nullable=True)
    image_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_micros: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
