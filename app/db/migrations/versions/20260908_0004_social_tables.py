"""Create pact, social, report, device, outbox, and account-deletion tables.

Revision ID: 20260908_0004
Revises: 20260908_0003
Create Date: 2026-09-08
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260908_0004"
down_revision: str | None = "20260908_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_JSONB = postgresql.JSONB(astext_type=sa.Text())
_NOW = sa.text("now()")
_UUID_DEFAULT = sa.text("gen_random_uuid()")


def _uuid_pk() -> Any:
    return sa.Column("id", _UUID, server_default=_UUID_DEFAULT, nullable=False)


def _created_at() -> Any:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False)


def _updated_at() -> Any:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False)


def upgrade() -> None:
    op.create_table(
        "npc_profiles",
        sa.Column("npc_id", sa.Text(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "public_marks",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("template_key", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("npc_id", "config_version", name="pk_npc_profiles"),
        sa.CheckConstraint(
            "char_length(npc_id) BETWEEN 1 AND 32",
            name="ck_npc_profiles_npc_id_len",
        ),
        sa.CheckConstraint("config_version >= 1", name="ck_npc_profiles_config_version"),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 32",
            name="ck_npc_profiles_display_name_len",
        ),
    )
    op.execute(
        """
        INSERT INTO public.npc_profiles (
          npc_id, config_version, display_name, title, template_key, enabled
        ) VALUES
          ('fog', 1, '雾里的那只', '雾里的那只', 'npc_fog_v1', true),
          ('lamp', 1, '守灯的那只', '守灯的那只', 'npc_lamp_v1', true),
          ('silent', 1, '不说话的那只', '不说话的那只', 'npc_silent_v1', true)
        """
    )

    op.create_table(
        "pacts",
        _uuid_pk(),
        sa.Column("spirit_id", _UUID, nullable=False),
        sa.Column("client_id", _UUID, nullable=False),
        sa.Column("theme", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("notes_memory_id", _UUID, nullable=True),
        sa.Column("question_bank_version", sa.Text(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("completed_sessions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("skipped_sessions", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completeness", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("scholar_mark", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["notes_memory_id"], ["memories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("theme IN ('interview', 'notes')", name="ck_pacts_theme"),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'abandoned')",
            name="ck_pacts_status",
        ),
        sa.CheckConstraint("completeness BETWEEN 0 AND 100", name="ck_pacts_completeness"),
        sa.CheckConstraint("completed_sessions >= 0", name="ck_pacts_completed_sessions"),
        sa.CheckConstraint("skipped_sessions >= 0", name="ck_pacts_skipped_sessions"),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 80", name="ck_pacts_title_length"),
        sa.CheckConstraint(
            "(theme <> 'notes') OR (notes_memory_id IS NOT NULL)",
            name="ck_pacts_notes_memory_required",
        ),
        sa.CheckConstraint("starts_at < ends_at", name="ck_pacts_starts_before_ends"),
    )
    op.create_index(
        "uq_pacts_spirit_one_active",
        "pacts",
        ["spirit_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "pact_sessions",
        _uuid_pk(),
        sa.Column("pact_id", _UUID, nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("day_index", sa.SmallInteger(), nullable=False),
        sa.Column("client_id", _UUID, nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'ready'"), nullable=False),
        sa.Column("explain", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("questions", _JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("answers", _JSONB, nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=True),
        sa.Column("feedback", _JSONB, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["pact_id"], ["pacts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pact_id", "session_date", name="uq_pact_sessions_pact_id_session_date"
        ),
        sa.UniqueConstraint("pact_id", "client_id", name="uq_pact_sessions_pact_id_client_id"),
        sa.CheckConstraint(
            "status IN ('ready', 'answered', 'skipped', 'closed')",
            name="ck_pact_sessions_status",
        ),
        sa.CheckConstraint("day_index BETWEEN 1 AND 7", name="ck_pact_sessions_day_index"),
        sa.CheckConstraint(
            "jsonb_typeof(questions) = 'array'",
            name="ck_pact_sessions_questions_array",
        ),
        sa.CheckConstraint(
            "answers IS NULL OR jsonb_typeof(answers) = 'array'",
            name="ck_pact_sessions_answers_array",
        ),
        sa.CheckConstraint(
            "feedback IS NULL OR jsonb_typeof(feedback) = 'object'",
            name="ck_pact_sessions_feedback_object",
        ),
    )

    op.create_table(
        "pact_mistakes",
        _uuid_pk(),
        sa.Column("pact_id", _UUID, nullable=False),
        sa.Column("session_id", _UUID, nullable=False),
        sa.Column("question_id", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("times_seen", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["pact_id"], ["pacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["pact_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id",
            "question_id",
            name="uq_pact_mistakes_session_id_question_id",
        ),
        sa.CheckConstraint(
            "char_length(summary) BETWEEN 1 AND 500",
            name="ck_pact_mistakes_summary_length",
        ),
        sa.CheckConstraint("times_seen >= 1", name="ck_pact_mistakes_times_seen"),
        sa.CheckConstraint(
            "char_length(question_id) BETWEEN 1 AND 128",
            name="ck_pact_mistakes_question_id_len",
        ),
    )

    op.create_table(
        "friends",
        _uuid_pk(),
        sa.Column("spirit_low_id", _UUID, nullable=False),
        sa.Column("spirit_high_id", _UUID, nullable=False),
        sa.Column("created_by_spirit_id", _UUID, nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["spirit_low_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["spirit_high_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "spirit_low_id",
            "spirit_high_id",
            name="uq_friends_spirit_low_id_spirit_high_id",
        ),
        sa.CheckConstraint("spirit_low_id < spirit_high_id", name="ck_friends_low_lt_high"),
        sa.CheckConstraint("status = 'active'", name="ck_friends_status_active"),
        sa.CheckConstraint(
            "created_by_spirit_id IN (spirit_low_id, spirit_high_id)",
            name="ck_friends_created_by_participant",
        ),
    )

    op.create_table(
        "visits",
        _uuid_pk(),
        sa.Column("client_id", _UUID, nullable=False),
        sa.Column("visitor_spirit_id", _UUID, nullable=False),
        sa.Column("host_spirit_id", _UUID, nullable=True),
        sa.Column("npc_id", sa.Text(), nullable=True),
        sa.Column("npc_config_version", sa.Integer(), nullable=True),
        sa.Column("plan_id", _UUID, nullable=False),
        sa.Column("destination_index", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("eligibility_key", sa.Text(), nullable=False),
        sa.Column("public_context", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["visitor_spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["host_spirit_id"], ["spirits.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["npc_id", "npc_config_version"],
            ["npc_profiles.npc_id", "npc_profiles.config_version"],
            name="fk_visits_npc_profile",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "destination_index",
            name="uq_visits_plan_id_destination_index",
        ),
        sa.UniqueConstraint("eligibility_key", name="uq_visits_eligibility_key"),
        sa.CheckConstraint(
            "status IN ('eligible', 'visiting', 'settled', 'cancelled', 'failed')",
            name="ck_visits_status",
        ),
        sa.CheckConstraint("destination_index IN (1, 2)", name="ck_visits_destination_index"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_visits_attempt_count"),
        sa.CheckConstraint(
            "(host_spirit_id IS NOT NULL AND npc_id IS NULL AND npc_config_version IS NULL) "
            "OR (host_spirit_id IS NULL AND npc_id IS NOT NULL AND npc_config_version IS NOT NULL)",
            name="ck_visits_host_xor_npc",
        ),
        sa.CheckConstraint(
            "host_spirit_id IS NULL OR host_spirit_id <> visitor_spirit_id",
            name="ck_visits_not_self",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(public_context) = 'object'",
            name="ck_visits_public_context_object",
        ),
    )
    op.create_index(
        "visits_due",
        "visits",
        ["status", "due_at"],
        postgresql_where=sa.text("status = 'visiting'"),
    )

    op.create_table(
        "postcards",
        _uuid_pk(),
        sa.Column("visit_id", _UUID, nullable=False),
        sa.Column("sender_spirit_id", _UUID, nullable=True),
        sa.Column("receiver_spirit_id", _UUID, nullable=False),
        sa.Column("npc_id", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(["visit_id"], ["visits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_spirit_id"], ["spirits.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["receiver_spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "visit_id",
            "receiver_spirit_id",
            name="uq_postcards_visit_id_receiver_spirit_id",
        ),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 300",
            name="ck_postcards_text_length",
        ),
    )
    op.execute(
        "CREATE INDEX postcards_unread ON public.postcards "
        "(receiver_spirit_id, created_at DESC, id DESC) WHERE read_at IS NULL"
    )

    op.create_table(
        "reports",
        _uuid_pk(),
        sa.Column("spirit_id", _UUID, nullable=False),
        sa.Column("report_type", sa.Text(), server_default=sa.text("'seven_day'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'generating'"), nullable=False),
        sa.Column(
            "eligibility_snapshot",
            _JSONB,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("spirit_snapshot", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("room_weather", sa.Text(), nullable=True),
        sa.Column("top_traits", _JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column(
            "top_memory_ids",
            postgresql.ARRAY(_UUID),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column(
            "top_memories_snapshot",
            _JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "scholar_marks",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("signature_line", sa.Text(), nullable=True),
        sa.Column("invite_code_snapshot", sa.Text(), nullable=True),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("line_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("spirit_id", "report_type", name="uq_reports_spirit_id_report_type"),
        sa.CheckConstraint("report_type = 'seven_day'", name="ck_reports_report_type"),
        sa.CheckConstraint(
            "status IN ('generating', 'ready', 'partial', 'failed')",
            name="ck_reports_status",
        ),
        sa.CheckConstraint("line_attempts >= 0", name="ck_reports_line_attempts"),
        sa.CheckConstraint(
            "jsonb_typeof(eligibility_snapshot) = 'object'",
            name="ck_reports_eligibility_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(spirit_snapshot) = 'object'",
            name="ck_reports_spirit_snapshot_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(top_traits) = 'array'",
            name="ck_reports_top_traits_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(top_memories_snapshot) = 'array'",
            name="ck_reports_top_memories_array",
        ),
    )

    op.create_table(
        "devices",
        _uuid_pk(),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("installation_id", _UUID, nullable=False),
        sa.Column("apns_token_hash", sa.Text(), nullable=False),
        sa.Column("apns_token_encrypted", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column(
            "notifications_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("app_version", sa.Text(), nullable=True),
        sa.Column("locale", sa.Text(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "environment",
            "apns_token_hash",
            name="uq_devices_environment_apns_token_hash",
        ),
        sa.UniqueConstraint(
            "user_id",
            "installation_id",
            "environment",
            name="uq_devices_user_id_installation_id_environment",
        ),
        sa.CheckConstraint(
            "environment IN ('sandbox', 'production')",
            name="ck_devices_environment",
        ),
        sa.CheckConstraint(
            "apns_token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_devices_apns_token_hash",
        ),
    )

    op.create_table(
        "notification_deliveries",
        _uuid_pk(),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("device_id", _UUID, nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("resource_id", _UUID, nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("apns_id", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'sent', 'suppressed', 'retry', 'dead')",
            name="ck_notification_deliveries_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_notification_deliveries_attempt_count"),
    )
    op.execute(
        "ALTER TABLE public.notification_deliveries "
        "ADD CONSTRAINT uq_notification_deliveries_dedupe_key_device_id "
        "UNIQUE NULLS NOT DISTINCT (dedupe_key, device_id)"
    )
    op.create_index(
        "notification_due",
        "notification_deliveries",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )

    op.create_table(
        "outbox_events",
        _uuid_pk(),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", _UUID, nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("owner_id", _UUID, nullable=True),
        sa.Column("payload", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("correlation_id", _UUID, server_default=_UUID_DEFAULT, nullable=False),
        _created_at(),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_outbox_events_dedupe_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'done', 'retry', 'dead')",
            name="ck_outbox_events_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_outbox_events_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_outbox_events_max_attempts"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_outbox_events_payload_object",
        ),
    )
    op.create_index(
        "outbox_due",
        "outbox_events",
        ["status", "available_at"],
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )

    op.create_table(
        "idempotency_records",
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("client_id", _UUID, nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", _UUID, nullable=True),
        sa.Column("response_json", _JSONB, nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "user_id",
            "operation",
            "client_id",
            name="pk_idempotency_records_user_operation_client",
        ),
        sa.CheckConstraint(
            "status IN ('in_progress', 'completed', 'conflict')",
            name="ck_idempotency_records_status",
        ),
        sa.CheckConstraint(
            "char_length(operation) BETWEEN 1 AND 64",
            name="ck_idempotency_records_operation_len",
        ),
    )

    op.create_table(
        "account_deletions",
        _uuid_pk(),
        sa.Column("owner_id", _UUID, nullable=False),
        sa.Column("client_id", _UUID, nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'accepted'"), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("storage_cursor", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("owner_hash", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "client_id",
            name="uq_account_deletions_owner_id_client_id",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'accepted', 'deleting_storage', 'deleting_auth', 'deleting_database', "
            "'completed', 'retry', 'dead'"
            ")",
            name="ck_account_deletions_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_account_deletions_attempts"),
    )

    for table in (
        "pacts",
        "pact_sessions",
        "pact_mistakes",
        "friends",
        "visits",
        "reports",
        "devices",
        "notification_deliveries",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_set_updated_at
            BEFORE UPDATE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
            """
        )


def downgrade() -> None:
    for table in (
        "notification_deliveries",
        "devices",
        "reports",
        "visits",
        "friends",
        "pact_mistakes",
        "pact_sessions",
        "pacts",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON public.{table}")
    op.drop_table("account_deletions")
    op.drop_table("idempotency_records")
    op.drop_table("outbox_events")
    op.drop_table("notification_deliveries")
    op.drop_table("devices")
    op.drop_table("reports")
    op.drop_table("postcards")
    op.drop_table("visits")
    op.drop_table("friends")
    op.drop_table("pact_mistakes")
    op.drop_table("pact_sessions")
    op.drop_table("pacts")
    op.drop_table("npc_profiles")
