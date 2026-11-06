"""V1 schema rebuild: split write-owned module tables after V0 chain.

Revision ID: 20260918_0100
Revises: 20260908_0019
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.db.rls_matrix import (
    RLS_MATRIX_V1,
    enable_force_statements,
    grant_statements,
    policy_blueprints,
    policy_create_statements,
)
from sqlalchemy.dialects import postgresql

revision: str = "20260918_0100"
down_revision: str | None = "20260908_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"
_RUNTIME = "kelin_api, kelin_worker, kelin_scheduler"
_V1_TABLES = (
    "spirit_awareness",
    "spirit_emotion",
    "spirit_growth",
    "chat_overlays",
    "room_projections",
    "action_events",
    "action_quotas",
)
_RETIRED_WRITE_TABLES = ("style_samples", "sight_uploads", "devices")
_UPDATED_AT_TABLES = (
    "spirit_awareness",
    "spirit_emotion",
    "spirit_growth",
    "chat_overlays",
    "room_projections",
    "action_quotas",
)
_STAGE_IMAGE = "stage_image_key IN ('whelp_image', 'formed_image', 'awake_image')"
_STATUS = "status IN ('home', 'away', 'study', 'lost')"


def _uuid_pk_default() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def _spirit_pk() -> sa.Column:
    return sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False)


def _ts_now(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _apply_rls() -> None:
    for statement in enable_force_statements(RLS_MATRIX_V1):
        op.execute(statement)
    for row in RLS_MATRIX_V1:
        op.execute(f"ALTER TABLE public.{row.table} OWNER TO kelin_migrator")
        op.execute(f"REVOKE ALL ON TABLE public.{row.table} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON TABLE public.{row.table} FROM {_RUNTIME}")
        op.execute(f"GRANT SELECT, DELETE ON TABLE public.{row.table} TO {_DEFINER}")
    for statement in policy_create_statements(RLS_MATRIX_V1):
        op.execute(statement)
    for statement in grant_statements(RLS_MATRIX_V1):
        op.execute(statement)


def _drop_rls() -> None:
    for blueprint in reversed(policy_blueprints(RLS_MATRIX_V1)):
        op.execute(f"DROP POLICY IF EXISTS {blueprint.name} ON public.{blueprint.table}")
    for row in RLS_MATRIX_V1:
        op.execute(f"ALTER TABLE public.{row.table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{row.table} DISABLE ROW LEVEL SECURITY")


def upgrade() -> None:
    op.add_column(
        "spirits",
        sa.Column(
            "snapshot_version",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_spirits_snapshot_version",
        "spirits",
        "snapshot_version >= 1",
    )
    op.execute("UPDATE public.spirits SET snapshot_version = version")

    op.add_column("memories", sa.Column("topic_key", sa.Text(), nullable=True))
    op.add_column(
        "memories",
        sa.Column("source_event_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column(
            "rules_version",
            sa.Text(),
            server_default=sa.text("'kelin-v1-rules-1'"),
            nullable=False,
        ),
    )
    op.add_column(
        "memories",
        sa.Column("confirm_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "memories",
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column(
            "decision_audit",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE public.memories
        SET type = CASE type
          WHEN 'relation' THEN 'relationship'
          WHEN 'speech' THEN 'knowledge'
          WHEN 'sight' THEN 'knowledge'
          ELSE type
        END
        """
    )
    op.drop_constraint("ck_memories_type", "memories", type_="check")
    op.create_check_constraint(
        "ck_memories_type",
        "memories",
        "type IN ('preference', 'knowledge', 'emotion', 'relationship')",
    )
    op.drop_constraint("ck_memories_status", "memories", type_="check")
    op.create_check_constraint(
        "ck_memories_status",
        "memories",
        "("
        "status = 'active' AND sealed_at IS NULL AND deleted_at IS NULL"
        ") OR ("
        "status = 'sealed' AND sealed_at IS NOT NULL AND deleted_at IS NULL"
        ") OR ("
        "status = 'deleted' AND deleted_at IS NOT NULL"
        ") OR ("
        "status IN ('superseded', 'stale') AND sealed_at IS NULL AND deleted_at IS NULL"
        ")",
    )
    op.create_check_constraint("ck_memories_confirm_count", "memories", "confirm_count >= 1")
    op.create_check_constraint(
        "ck_memories_topic_key",
        "memories",
        "topic_key IS NULL OR char_length(topic_key) BETWEEN 1 AND 128",
    )
    op.create_check_constraint(
        "ck_memories_decision_audit_object",
        "memories",
        "jsonb_typeof(decision_audit) = 'object'",
    )
    op.create_check_constraint(
        "ck_memories_rules_version",
        "memories",
        "char_length(rules_version) BETWEEN 1 AND 64",
    )
    op.create_index(
        "uq_memories_spirit_id_source_event_id",
        "memories",
        ["spirit_id", "source_event_id"],
        unique=True,
        postgresql_where=sa.text("source_event_id IS NOT NULL"),
    )
    op.create_index(
        "memories_topic_active",
        "memories",
        ["spirit_id", "topic_key"],
        unique=False,
        postgresql_where=sa.text("status = 'active' AND topic_key IS NOT NULL"),
    )

    op.add_column("growth_events", sa.Column("decision_reason", sa.Text(), nullable=True))
    op.add_column(
        "growth_events",
        sa.Column(
            "rules_version",
            sa.Text(),
            server_default=sa.text("'kelin-v1-rules-1'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_growth_events_decision_reason",
        "growth_events",
        "decision_reason IS NULL OR char_length(decision_reason) BETWEEN 1 AND 64",
    )
    op.create_check_constraint(
        "ck_growth_events_rules_version",
        "growth_events",
        "char_length(rules_version) BETWEEN 1 AND 64",
    )

    op.create_table(
        "spirit_awareness",
        _spirit_pk(),
        sa.Column("status", sa.Text(), server_default=sa.text("'home'"), nullable=False),
        sa.Column("hunger", sa.SmallInteger(), server_default=sa.text("80"), nullable=False),
        sa.Column("energy", sa.SmallInteger(), server_default=sa.text("80"), nullable=False),
        _ts_now("last_interact_at"),
        sa.Column("study_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("away_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("visit_cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("has_been_lost", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _ts_now("last_state_settled_at"),
        _ts_now("updated_at"),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("spirit_id"),
        sa.CheckConstraint(_STATUS, name="ck_spirit_awareness_status"),
        sa.CheckConstraint(
            "hunger BETWEEN 0 AND 100 AND energy BETWEEN 0 AND 100",
            name="ck_spirit_awareness_hunger_energy",
        ),
    )
    op.create_table(
        "spirit_emotion",
        _spirit_pk(),
        sa.Column("mood", sa.SmallInteger(), server_default=sa.text("60"), nullable=False),
        sa.Column("realtime_emotion", sa.Text(), nullable=True),
        sa.Column("emotion_reason_code", sa.Text(), nullable=True),
        sa.Column("emotion_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emotion_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emotion_source_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("primary_label", sa.Text(), nullable=True),
        sa.Column(
            "emotion_snapshot_version",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        _ts_now("updated_at"),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("spirit_id"),
        sa.CheckConstraint("mood BETWEEN 0 AND 100", name="ck_spirit_emotion_mood"),
        sa.CheckConstraint(
            "primary_label IS NULL OR char_length(primary_label) BETWEEN 1 AND 32",
            name="ck_spirit_emotion_primary_label",
        ),
        sa.CheckConstraint(
            "emotion_snapshot_version >= 1",
            name="ck_spirit_emotion_snapshot_version",
        ),
    )
    op.create_table(
        "spirit_growth",
        _spirit_pk(),
        sa.Column("closeness", sa.SmallInteger(), nullable=False),
        sa.Column("curiosity", sa.SmallInteger(), nullable=False),
        sa.Column("sharpness", sa.SmallInteger(), nullable=False),
        sa.Column("nocturnal", sa.SmallInteger(), nullable=False),
        sa.Column("stubborn", sa.SmallInteger(), nullable=False),
        sa.Column("bond", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("stage", sa.Text(), server_default=sa.text("'whelp'"), nullable=False),
        sa.Column(
            "stage_image_key",
            sa.Text(),
            server_default=sa.text("'whelp_image'"),
            nullable=False,
        ),
        sa.Column("cognition", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("has_visited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("trait_delta_day", sa.Date(), nullable=True),
        sa.Column(
            "trait_abs_delta_today",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_decision_reason", sa.Text(), nullable=True),
        _ts_now("updated_at"),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("spirit_id"),
        sa.CheckConstraint(
            "closeness BETWEEN 0 AND 100 AND curiosity BETWEEN 0 AND 100 "
            "AND sharpness BETWEEN 0 AND 100 AND nocturnal BETWEEN 0 AND 100 "
            "AND stubborn BETWEEN 0 AND 100 AND bond BETWEEN 0 AND 100",
            name="ck_spirit_growth_trait_ranges",
        ),
        sa.CheckConstraint("stage IN ('whelp', 'formed', 'awake')", name="ck_spirit_growth_stage"),
        sa.CheckConstraint(_STAGE_IMAGE, name="ck_spirit_growth_stage_image_key"),
        sa.CheckConstraint("cognition >= 0", name="ck_spirit_growth_cognition"),
        sa.CheckConstraint(
            "trait_abs_delta_today >= 0 AND trait_abs_delta_today <= 4",
            name="ck_spirit_growth_daily_trait",
        ),
    )
    op.create_table(
        "chat_overlays",
        _spirit_pk(),
        sa.Column("overlay_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _ts_now("updated_at"),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("spirit_id"),
        sa.CheckConstraint("overlay_version >= 1", name="ck_chat_overlays_version"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_chat_overlays_payload_object",
        ),
    )
    op.create_table(
        "room_projections",
        _spirit_pk(),
        sa.Column(
            "stage_image_key",
            sa.Text(),
            server_default=sa.text("'whelp_image'"),
            nullable=False,
        ),
        sa.Column("primary_emotion_label", sa.Text(), nullable=True),
        sa.Column("letter_key", sa.Text(), nullable=True),
        sa.Column("text_status", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'home'"), nullable=False),
        sa.Column("snapshot_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        _ts_now("updated_at"),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("spirit_id"),
        sa.CheckConstraint(_STAGE_IMAGE, name="ck_room_projections_stage_image_key"),
        sa.CheckConstraint(_STATUS, name="ck_room_projections_status"),
        sa.CheckConstraint("snapshot_version >= 1", name="ck_room_projections_snapshot_version"),
        sa.CheckConstraint(
            "primary_emotion_label IS NULL OR char_length(primary_emotion_label) BETWEEN 1 AND 32",
            name="ck_room_projections_primary_label",
        ),
    )
    op.create_table(
        "action_events",
        _uuid_pk_default(),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _ts_now("occurred_at"),
        _ts_now("created_at"),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("spirit_id", "event_id", name="uq_action_events_spirit_event_id"),
        sa.UniqueConstraint("spirit_id", "client_id", name="uq_action_events_spirit_client_id"),
        sa.CheckConstraint(
            "status IN ('accepted', 'rejected', 'conflict')",
            name="ck_action_events_status",
        ),
        sa.CheckConstraint("char_length(kind) BETWEEN 1 AND 64", name="ck_action_events_kind"),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_action_events_payload_object",
        ),
    )
    op.create_table(
        "action_quotas",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("used", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("reserved", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("limit_value", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        _ts_now("updated_at"),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["spirit_id"], ["spirits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "usage_date", "capability"),
        sa.CheckConstraint(
            "capability IN ("
            "'chat', 'asr', 'tts', 'search', 'food', "
            "'knowledge', 'emotion', 'visit', 'pact', 'recall'"
            ")",
            name="ck_action_quotas_capability",
        ),
        sa.CheckConstraint("used >= 0", name="ck_action_quotas_used"),
        sa.CheckConstraint("reserved >= 0", name="ck_action_quotas_reserved"),
        sa.CheckConstraint("limit_value >= 0", name="ck_action_quotas_limit_value"),
    )

    op.execute(
        """
        INSERT INTO public.spirit_awareness (
          spirit_id, status, hunger, energy, last_interact_at, study_until, away_until,
          visit_cooldown_until, has_been_lost, last_state_settled_at
        )
        SELECT
          id, status, hunger, energy, last_interact_at, study_until, away_until,
          NULL, has_been_lost, last_interact_at
        FROM public.spirits
        """
    )
    op.execute(
        """
        INSERT INTO public.spirit_emotion (
          spirit_id, mood, primary_label, emotion_snapshot_version
        )
        SELECT id, mood, '平静', version
        FROM public.spirits
        """
    )
    op.execute(
        """
        INSERT INTO public.spirit_growth (
          spirit_id, closeness, curiosity, sharpness, nocturnal, stubborn, bond, stage,
          stage_image_key, has_visited
        )
        SELECT
          id, closeness, curiosity, sharpness, nocturnal, stubborn, bond, stage,
          CASE stage
            WHEN 'formed' THEN 'formed_image'
            WHEN 'awake' THEN 'awake_image'
            ELSE 'whelp_image'
          END,
          has_visited
        FROM public.spirits
        """
    )
    op.execute(
        """
        INSERT INTO public.chat_overlays (spirit_id)
        SELECT id FROM public.spirits
        """
    )
    op.execute(
        """
        INSERT INTO public.room_projections (
          spirit_id, stage_image_key, primary_emotion_label, status, snapshot_version
        )
        SELECT
          id,
          CASE stage
            WHEN 'formed' THEN 'formed_image'
            WHEN 'awake' THEN 'awake_image'
            ELSE 'whelp_image'
          END,
          '平静',
          status,
          version
        FROM public.spirits
        """
    )

    for table in _UPDATED_AT_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_set_updated_at
            BEFORE UPDATE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
            """
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.v1_block_retired_write()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          RAISE EXCEPTION 'v1 retired write path: %', TG_TABLE_NAME
            USING ERRCODE = '42501';
        END
        $$
        """
    )
    op.execute("ALTER FUNCTION private.v1_block_retired_write() OWNER TO kelin_migrator")
    op.execute("REVOKE ALL ON FUNCTION private.v1_block_retired_write() FROM PUBLIC")
    for table in _RETIRED_WRITE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_v1_block_write
            BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION private.v1_block_retired_write()
            """
        )
    op.execute(
        """
        CREATE TRIGGER feeds_v1_block_sight
        BEFORE INSERT OR UPDATE ON public.feeds
        FOR EACH ROW
        WHEN (NEW.kind = 'sight')
        EXECUTE FUNCTION private.v1_block_retired_write()
        """
    )

    _apply_rls()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS feeds_v1_block_sight ON public.feeds")
    for table in _RETIRED_WRITE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_v1_block_write ON public.{table}")
    op.execute("DROP FUNCTION IF EXISTS private.v1_block_retired_write()")
    for table in _UPDATED_AT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_set_updated_at ON public.{table}")

    _drop_rls()
    for table in reversed(_V1_TABLES):
        op.drop_table(table)

    op.drop_constraint("ck_growth_events_rules_version", "growth_events", type_="check")
    op.drop_constraint("ck_growth_events_decision_reason", "growth_events", type_="check")
    op.drop_column("growth_events", "rules_version")
    op.drop_column("growth_events", "decision_reason")

    op.drop_index("memories_topic_active", table_name="memories")
    op.drop_index("uq_memories_spirit_id_source_event_id", table_name="memories")
    op.drop_constraint("ck_memories_rules_version", "memories", type_="check")
    op.drop_constraint("ck_memories_decision_audit_object", "memories", type_="check")
    op.drop_constraint("ck_memories_topic_key", "memories", type_="check")
    op.drop_constraint("ck_memories_confirm_count", "memories", type_="check")
    op.drop_constraint("ck_memories_status", "memories", type_="check")
    op.create_check_constraint(
        "ck_memories_status",
        "memories",
        "("
        "status = 'active' AND sealed_at IS NULL AND deleted_at IS NULL"
        ") OR ("
        "status = 'sealed' AND sealed_at IS NOT NULL AND deleted_at IS NULL"
        ") OR ("
        "status = 'deleted' AND deleted_at IS NOT NULL"
        ")",
    )
    op.drop_constraint("ck_memories_type", "memories", type_="check")
    op.execute(
        """
        UPDATE public.memories
        SET type = CASE type WHEN 'relationship' THEN 'relation' ELSE type END
        """
    )
    op.create_check_constraint(
        "ck_memories_type",
        "memories",
        "type IN ('preference', 'knowledge', 'emotion', 'relation', 'speech', 'sight')",
    )
    op.drop_column("memories", "decision_audit")
    op.drop_column("memories", "last_confirmed_at")
    op.drop_column("memories", "confirm_count")
    op.drop_column("memories", "rules_version")
    op.drop_column("memories", "source_event_id")
    op.drop_column("memories", "topic_key")

    op.drop_constraint("ck_spirits_snapshot_version", "spirits", type_="check")
    op.drop_column("spirits", "snapshot_version")
