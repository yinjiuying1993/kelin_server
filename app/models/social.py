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
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    Text,
    UniqueConstraint,
    desc,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text as sql_text

from app.db.base import Base


class NpcProfile(Base):
    __tablename__ = "npc_profiles"
    __table_args__ = (
        PrimaryKeyConstraint("npc_id", "config_version", name="pk_npc_profiles"),
        CheckConstraint("char_length(npc_id) BETWEEN 1 AND 32", name="ck_npc_profiles_npc_id_len"),
        CheckConstraint("config_version >= 1", name="ck_npc_profiles_config_version"),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 32",
            name="ck_npc_profiles_display_name_len",
        ),
        {"schema": "public"},
    )

    npc_id: Mapped[str] = mapped_column(Text, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    public_marks: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
    )
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class Pact(Base):
    __tablename__ = "pacts"
    __table_args__ = (
        CheckConstraint("theme IN ('interview', 'notes')", name="ck_pacts_theme"),
        CheckConstraint(
            "status IN ('active', 'completed', 'abandoned')",
            name="ck_pacts_status",
        ),
        CheckConstraint("completeness BETWEEN 0 AND 100", name="ck_pacts_completeness"),
        CheckConstraint("completed_sessions >= 0", name="ck_pacts_completed_sessions"),
        CheckConstraint("skipped_sessions >= 0", name="ck_pacts_skipped_sessions"),
        CheckConstraint("char_length(title) BETWEEN 1 AND 80", name="ck_pacts_title_length"),
        CheckConstraint(
            "(theme <> 'notes') OR (notes_memory_id IS NOT NULL)",
            name="ck_pacts_notes_memory_required",
        ),
        CheckConstraint("starts_at < ends_at", name="ck_pacts_starts_before_ends"),
        Index(
            "uq_pacts_spirit_one_active",
            "spirit_id",
            unique=True,
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
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    theme: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    notes_memory_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("memories.id", ondelete="SET NULL"),
        nullable=True,
    )
    question_bank_version: Mapped[str] = mapped_column(Text, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'active'"))
    completed_sessions: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    skipped_sessions: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    completeness: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=sql_text("0")
    )
    scholar_mark: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class PactSession(Base):
    __tablename__ = "pact_sessions"
    __table_args__ = (
        UniqueConstraint("pact_id", "session_date", name="uq_pact_sessions_pact_id_session_date"),
        UniqueConstraint("pact_id", "client_id", name="uq_pact_sessions_pact_id_client_id"),
        CheckConstraint(
            "status IN ('ready', 'answered', 'skipped', 'closed')",
            name="ck_pact_sessions_status",
        ),
        CheckConstraint("day_index BETWEEN 1 AND 7", name="ck_pact_sessions_day_index"),
        CheckConstraint(
            "jsonb_typeof(questions) = 'array'",
            name="ck_pact_sessions_questions_array",
        ),
        CheckConstraint(
            "answers IS NULL OR jsonb_typeof(answers) = 'array'",
            name="ck_pact_sessions_answers_array",
        ),
        CheckConstraint(
            "feedback IS NULL OR jsonb_typeof(feedback) = 'object'",
            name="ck_pact_sessions_feedback_object",
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    pact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    day_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'ready'"))
    explain: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("''"))
    questions: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb"),
    )
    answers: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    feedback: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    skipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class PactMistake(Base):
    __tablename__ = "pact_mistakes"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "question_id",
            name="uq_pact_mistakes_session_id_question_id",
        ),
        CheckConstraint(
            "char_length(summary) BETWEEN 1 AND 500",
            name="ck_pact_mistakes_summary_length",
        ),
        CheckConstraint("times_seen >= 1", name="ck_pact_mistakes_times_seen"),
        CheckConstraint(
            "char_length(question_id) BETWEEN 1 AND 128",
            name="ck_pact_mistakes_question_id_len",
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    pact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pact_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("1"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class Friend(Base):
    __tablename__ = "friends"
    __table_args__ = (
        UniqueConstraint(
            "spirit_low_id",
            "spirit_high_id",
            name="uq_friends_spirit_low_id_spirit_high_id",
        ),
        CheckConstraint("spirit_low_id < spirit_high_id", name="ck_friends_low_lt_high"),
        CheckConstraint("status = 'active'", name="ck_friends_status_active"),
        CheckConstraint(
            "created_by_spirit_id IN (spirit_low_id, spirit_high_id)",
            name="ck_friends_created_by_participant",
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    spirit_low_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    spirit_high_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_by_spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'active'"))
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


class Visit(Base):
    __tablename__ = "visits"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "destination_index",
            name="uq_visits_plan_id_destination_index",
        ),
        UniqueConstraint("eligibility_key", name="uq_visits_eligibility_key"),
        CheckConstraint(
            "status IN ('eligible', 'visiting', 'settled', 'cancelled', 'failed')",
            name="ck_visits_status",
        ),
        CheckConstraint("destination_index IN (1, 2)", name="ck_visits_destination_index"),
        CheckConstraint("attempt_count >= 0", name="ck_visits_attempt_count"),
        CheckConstraint(
            "(host_spirit_id IS NOT NULL AND npc_id IS NULL AND npc_config_version IS NULL) "
            "OR (host_spirit_id IS NULL AND npc_id IS NOT NULL AND npc_config_version IS NOT NULL)",
            name="ck_visits_host_xor_npc",
        ),
        CheckConstraint(
            "host_spirit_id IS NULL OR host_spirit_id <> visitor_spirit_id",
            name="ck_visits_not_self",
        ),
        CheckConstraint(
            "jsonb_typeof(public_context) = 'object'",
            name="ck_visits_public_context_object",
        ),
        ForeignKeyConstraint(
            ["npc_id", "npc_config_version"],
            ["npc_profiles.npc_id", "npc_profiles.config_version"],
            name="fk_visits_npc_profile",
        ),
        Index(
            "visits_due",
            "status",
            "due_at",
            postgresql_where=sql_text("status = 'visiting'"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    visitor_spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    host_spirit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="RESTRICT"),
        nullable=True,
    )
    npc_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    npc_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    destination_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_key: Mapped[str] = mapped_column(Text, nullable=False)
    public_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
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


class Postcard(Base):
    __tablename__ = "postcards"
    __table_args__ = (
        UniqueConstraint(
            "visit_id",
            "receiver_spirit_id",
            name="uq_postcards_visit_id_receiver_spirit_id",
        ),
        CheckConstraint("char_length(text) BETWEEN 1 AND 300", name="ck_postcards_text_length"),
        Index(
            "postcards_unread",
            "receiver_spirit_id",
            desc("created_at"),
            desc("id"),
            postgresql_where=sql_text("read_at IS NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    visit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("visits.id", ondelete="CASCADE"),
        nullable=False,
    )
    sender_spirit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="SET NULL"),
        nullable=True,
    )
    receiver_spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    npc_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("spirit_id", "report_type", name="uq_reports_spirit_id_report_type"),
        CheckConstraint("report_type = 'seven_day'", name="ck_reports_report_type"),
        CheckConstraint(
            "status IN ('generating', 'ready', 'partial', 'failed')",
            name="ck_reports_status",
        ),
        CheckConstraint("line_attempts >= 0 AND line_attempts <= 3", name="ck_reports_line_attempts"),
        CheckConstraint(
            "jsonb_typeof(eligibility_snapshot) = 'object'",
            name="ck_reports_eligibility_object",
        ),
        CheckConstraint(
            "jsonb_typeof(spirit_snapshot) = 'object'",
            name="ck_reports_spirit_snapshot_object",
        ),
        CheckConstraint("jsonb_typeof(top_traits) = 'array'", name="ck_reports_top_traits_array"),
        CheckConstraint(
            "jsonb_typeof(top_memories_snapshot) = 'array'",
            name="ck_reports_top_memories_array",
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
    report_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'seven_day'")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'generating'")
    )
    eligibility_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    spirit_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    room_weather: Mapped[str | None] = mapped_column(Text, nullable=True)
    top_traits: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb"),
    )
    top_memory_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        server_default=sql_text("'{}'::uuid[]"),
    )
    top_memories_snapshot: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'[]'::jsonb"),
    )
    scholar_marks: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=sql_text("'{}'::text[]"),
    )
    signature_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    invite_code_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    line_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint(
            "environment",
            "apns_token_hash",
            name="uq_devices_environment_apns_token_hash",
        ),
        UniqueConstraint(
            "user_id",
            "installation_id",
            "environment",
            name="uq_devices_user_id_installation_id_environment",
        ),
        CheckConstraint(
            "environment IN ('sandbox', 'production')",
            name="ck_devices_environment",
        ),
        CheckConstraint(
            "apns_token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_devices_apns_token_hash",
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
    installation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    apns_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    apns_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("true")
    )
    app_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    locale: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "dedupe_key",
            "device_id",
            name="uq_notification_deliveries_dedupe_key_device_id",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'sent', 'suppressed', 'retry', 'dead')",
            name="ck_notification_deliveries_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_notification_deliveries_attempt_count"),
        Index(
            "notification_due",
            "status",
            "next_attempt_at",
            postgresql_where=sql_text("status IN ('pending', 'retry')"),
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
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    apns_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_outbox_events_dedupe_key"),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'done', 'retry', 'dead')",
            name="ck_outbox_events_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_outbox_events_max_attempts"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_outbox_events_payload_object"),
        Index(
            "outbox_due",
            "status",
            "available_at",
            postgresql_where=sql_text("status IN ('pending', 'retry')"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'pending'"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sql_text("0")
    )
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("8"))
    locked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        server_default=sql_text("gen_random_uuid()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        PrimaryKeyConstraint(
            "user_id",
            "operation",
            "client_id",
            name="pk_idempotency_records_user_operation_client",
        ),
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'conflict')",
            name="ck_idempotency_records_status",
        ),
        CheckConstraint(
            "char_length(operation) BETWEEN 1 AND 64",
            name="ck_idempotency_records_operation_len",
        ),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccountDeletion(Base):
    __tablename__ = "account_deletions"
    __table_args__ = (
        UniqueConstraint("owner_id", "client_id", name="uq_account_deletions_owner_id_client_id"),
        CheckConstraint(
            "status IN ("
            "'accepted', 'deleting_storage', 'deleting_auth', 'deleting_database', "
            "'completed', 'retry', 'dead'"
            ")",
            name="ck_account_deletions_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_account_deletions_attempts"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sql_text("gen_random_uuid()"),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sql_text("'accepted'"))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sql_text("now()"),
    )
    storage_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
