"""Create chat, memory, feed, upload, and usage tables.

Revision ID: 20260908_0003
Revises: 20260908_0002
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260908_0003"
down_revision: str | None = "20260908_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_windows",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("end_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_round_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("onboarding", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("extract_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("extract_client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_hash", sa.Text(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "spirit_id",
            "start_message_id",
            "end_message_id",
            name="uq_conversation_windows_spirit_start_end",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'ready', 'extracting', 'extracted', 'failed')",
            name="ck_conversation_windows_status",
        ),
        sa.CheckConstraint(
            "extract_attempts >= 0",
            name="ck_conversation_windows_extract_attempts",
        ),
    )
    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_window_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("onboarding", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reply_to_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("provider_request_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["conversation_window_id"],
            ["conversation_windows.id"],
            ondelete="SET NULL",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["reply_to_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("role IN ('user', 'spirit', 'system')", name="ck_messages_role"),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 4000",
            name="ck_messages_content_length",
        ),
        sa.CheckConstraint("source IN ('text', 'voice', 'onboarding')", name="ck_messages_source"),
        sa.CheckConstraint(
            "status IN ('accepted', 'generated', 'failed')",
            name="ck_messages_status",
        ),
        sa.CheckConstraint(
            "(role = 'user' AND client_id IS NOT NULL) "
            "OR (role IN ('spirit', 'system') AND client_id IS NULL)",
            name="ck_messages_user_client_id",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_refs) = 'array'",
            name="ck_messages_source_refs_array",
        ),
    )
    op.create_foreign_key(
        "fk_conversation_windows_start_message_id",
        "conversation_windows",
        "messages",
        ["start_message_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_conversation_windows_end_message_id",
        "conversation_windows",
        "messages",
        ["end_message_id"],
        ["id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "uq_conversation_windows_spirit_extract_client_id",
        "conversation_windows",
        ["spirit_id", "extract_client_id"],
        unique=True,
        postgresql_where=sa.text("extract_client_id IS NOT NULL"),
    )
    op.create_index(
        "windows_ready",
        "conversation_windows",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('ready', 'failed')"),
    )
    op.create_index(
        "uq_messages_spirit_id_client_id",
        "messages",
        ["spirit_id", "client_id"],
        unique=True,
        postgresql_where=sa.text("client_id IS NOT NULL"),
    )
    op.execute(
        "CREATE INDEX messages_page ON public.messages (spirit_id, created_at DESC, id DESC)"
    )

    op.create_table(
        "feeds",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("rejection_code", sa.Text(), nullable=True),
        sa.Column("effect_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("promise_status", sa.Text(), nullable=True),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_id", name="uq_feeds_user_id_client_id"),
        sa.CheckConstraint(
            "kind IN ('food', 'sight', 'knowledge', 'emotion', 'promise')",
            name="ck_feeds_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'accepted', 'rejected', 'cancelled')",
            name="ck_feeds_status",
        ),
        sa.CheckConstraint(
            "(kind <> 'promise' AND promise_status IS NULL) "
            "OR (kind = 'promise' AND promise_status IN ('active', 'completed', 'cancelled'))",
            name="ck_feeds_promise_status",
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_feeds_payload_object"),
    )
    op.create_index(
        "uq_feeds_spirit_one_active_promise",
        "feeds",
        ["spirit_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'promise' AND promise_status = 'active'"),
    )
    op.create_index(
        "feeds_pending",
        "feeds",
        ["status", "updated_at"],
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    op.create_table(
        "memories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("salience", sa.SmallInteger(), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_feed_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("corrected_from_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_feed_id"], ["feeds.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["corrected_from_id"], ["memories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "type IN ('preference', 'knowledge', 'emotion', 'relation', 'speech', 'sight')",
            name="ck_memories_type",
        ),
        sa.CheckConstraint(
            "char_length(summary) BETWEEN 1 AND 500",
            name="ck_memories_summary_length",
        ),
        sa.CheckConstraint("salience BETWEEN 0 AND 100", name="ck_memories_salience"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memories_confidence"),
        sa.CheckConstraint(
            "(status = 'active' AND sealed_at IS NULL AND deleted_at IS NULL) "
            "OR (status = 'sealed' AND sealed_at IS NOT NULL AND deleted_at IS NULL) "
            "OR (status = 'deleted' AND deleted_at IS NOT NULL)",
            name="ck_memories_status",
        ),
    )
    op.execute(
        "CREATE INDEX memories_active_page ON public.memories "
        "(spirit_id, created_at DESC, id DESC) WHERE status = 'active'"
    )

    op.create_table(
        "style_samples",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("weight", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("source_window_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_window_id"], ["conversation_windows.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "spirit_id",
            "kind",
            "text",
            name="uq_style_samples_spirit_kind_text",
        ),
        sa.CheckConstraint(
            "kind IN ('user_dialect', 'user_filler', 'spirit_catchphrase')",
            name="ck_style_samples_kind",
        ),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 100",
            name="ck_style_samples_text_length",
        ),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_style_samples_status"),
    )

    op.create_table(
        "sight_uploads",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feed_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column("object_path", sa.Text(), nullable=False),
        sa.Column(
            "expected_mime",
            sa.Text(),
            server_default=sa.text("'image/jpeg'"),
            nullable=False,
        ),
        sa.Column("expected_size", sa.Integer(), nullable=False),
        sa.Column("expected_sha256", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'issued'"), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(now() + interval '10 minutes')"),
            nullable=False,
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_size", sa.Integer(), nullable=True),
        sa.Column("actual_sha256", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "client_id", name="uq_sight_uploads_user_id_client_id"),
        sa.UniqueConstraint("bucket", "object_path", name="uq_sight_uploads_bucket_object_path"),
        sa.CheckConstraint(
            "status IN ('issued', 'uploaded', 'verifying', 'consumed', 'expired', 'rejected')",
            name="ck_sight_uploads_status",
        ),
        sa.CheckConstraint("expected_mime = 'image/jpeg'", name="ck_sight_uploads_expected_mime"),
        sa.CheckConstraint(
            "expected_size BETWEEN 1 AND 5242880",
            name="ck_sight_uploads_expected_size",
        ),
        sa.CheckConstraint(
            "expected_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_sight_uploads_expected_sha256",
        ),
    )
    op.create_index(
        "uploads_expire",
        "sight_uploads",
        ["status", "expires_at"],
        postgresql_where=sa.text("status IN ('issued', 'uploaded', 'verifying')"),
    )
    op.execute(
        """
        CREATE FUNCTION public.sight_uploads_require_sight_feed()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          feed_kind text;
        BEGIN
          SELECT kind INTO feed_kind FROM public.feeds WHERE id = NEW.feed_id;
          IF feed_kind IS DISTINCT FROM 'sight' THEN
            RAISE EXCEPTION 'sight_uploads.feed_id must reference a sight feed'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER sight_uploads_require_sight_feed
        BEFORE INSERT OR UPDATE OF feed_id ON public.sight_uploads
        FOR EACH ROW EXECUTE FUNCTION public.sight_uploads_require_sight_feed()
        """
    )

    op.create_table(
        "growth_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "event_type",
            name="uq_growth_events_source_type_source_id_event_type",
        ),
        sa.CheckConstraint(
            "event_type IN ("
            "'chat_completed', 'feed_accepted', 'promise_completed', "
            "'pact_answered', 'pact_completed', 'memory_added', "
            "'visit_completed', 'returned_from_lost', 'time_passed'"
            ")",
            name="ck_growth_events_event_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_growth_events_payload_object",
        ),
    )

    op.create_table(
        "daily_usage",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("used", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("reserved", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("limit_value", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "usage_date", "capability"),
        sa.CheckConstraint(
            "capability IN ("
            "'chat', 'asr', 'tts', 'sight', 'search', 'food', "
            "'knowledge', 'emotion', 'visit', 'pact'"
            ")",
            name="ck_daily_usage_capability",
        ),
        sa.CheckConstraint("used >= 0", name="ck_daily_usage_used"),
        sa.CheckConstraint("reserved >= 0", name="ck_daily_usage_reserved"),
        sa.CheckConstraint("limit_value >= 0", name="ck_daily_usage_limit_value"),
    )

    op.create_table(
        "ai_usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("model_alias", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("input_units", sa.BigInteger(), nullable=True),
        sa.Column("output_units", sa.BigInteger(), nullable=True),
        sa.Column("audio_seconds", sa.Numeric(8, 3), nullable=True),
        sa.Column("image_count", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("provider_request_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    for table in (
        "conversation_windows",
        "messages",
        "feeds",
        "memories",
        "style_samples",
        "sight_uploads",
        "daily_usage",
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
        "daily_usage",
        "sight_uploads",
        "style_samples",
        "memories",
        "feeds",
        "messages",
        "conversation_windows",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON public.{table}")
    op.execute("DROP TRIGGER IF EXISTS sight_uploads_require_sight_feed ON public.sight_uploads")
    op.execute("DROP FUNCTION IF EXISTS public.sight_uploads_require_sight_feed()")
    op.drop_table("ai_usage")
    op.drop_table("daily_usage")
    op.drop_table("growth_events")
    op.drop_table("sight_uploads")
    op.drop_table("style_samples")
    op.drop_table("memories")
    op.drop_table("feeds")
    op.drop_constraint(
        "fk_conversation_windows_start_message_id",
        "conversation_windows",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_conversation_windows_end_message_id",
        "conversation_windows",
        type_="foreignkey",
    )
    op.drop_table("messages")
    op.drop_table("conversation_windows")
