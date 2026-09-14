"""Create account_consents, user_preferences, and spirits.

Revision ID: 20260908_0002
Revises: 20260908_0001
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260908_0002"
down_revision: str | None = "20260908_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS auth")
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="auth",
    )
    op.create_table(
        "account_consents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consent_type", sa.Text(), nullable=False),
        sa.Column("document_version", sa.Text(), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locale", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), server_default=sa.text("'ios'"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "consent_type",
            "document_version",
            name="uq_account_consents_user_type_version",
        ),
        sa.CheckConstraint(
            "consent_type IN ('ai_disclosure', 'user_terms', 'data_notice')",
            name="ck_account_consents_type",
        ),
        sa.CheckConstraint(
            "char_length(document_version) BETWEEN 1 AND 32",
            name="ck_account_consents_document_version_len",
        ),
        sa.CheckConstraint("source = 'ios'", name="ck_account_consents_source_ios"),
    )
    op.create_table(
        "user_preferences",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tts_on", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("push_on", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("visit_on", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("dnd_start", sa.Time(), server_default=sa.text("'23:00:00'"), nullable=False),
        sa.Column("dnd_end", sa.Time(), server_default=sa.text("'08:00:00'"), nullable=False),
        sa.Column("timezone", sa.Text(), server_default=sa.text("'Asia/Shanghai'"), nullable=False),
        sa.Column("default_city", sa.Text(), nullable=True),
        sa.Column(
            "location_weather_on",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("remote_search_on", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "spirits",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), server_default=sa.text("'未名'"), nullable=False),
        sa.Column("egg", sa.Text(), nullable=False),
        sa.Column("invite_code", sa.Text(), nullable=False),
        sa.Column("closeness", sa.SmallInteger(), nullable=False),
        sa.Column("curiosity", sa.SmallInteger(), nullable=False),
        sa.Column("sharpness", sa.SmallInteger(), nullable=False),
        sa.Column("nocturnal", sa.SmallInteger(), nullable=False),
        sa.Column("stubborn", sa.SmallInteger(), nullable=False),
        sa.Column("hunger", sa.SmallInteger(), server_default=sa.text("80"), nullable=False),
        sa.Column("energy", sa.SmallInteger(), server_default=sa.text("80"), nullable=False),
        sa.Column("mood", sa.SmallInteger(), server_default=sa.text("60"), nullable=False),
        sa.Column("bond", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("stage", sa.Text(), server_default=sa.text("'whelp'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'home'"), nullable=False),
        sa.Column(
            "scholar_marks",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "onboarding_step", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ordinary_dialogue_rounds", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("away_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("study_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_interact_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("hatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("has_visited", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("has_been_lost", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
        sa.UniqueConstraint("invite_code"),
        sa.UniqueConstraint("user_id", "client_id", name="uq_spirits_user_id_client_id"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 20", name="ck_spirits_name_length"),
        sa.CheckConstraint("egg IN ('warm', 'cold', 'wild')", name="ck_spirits_egg"),
        sa.CheckConstraint("stage IN ('whelp', 'formed', 'awake')", name="ck_spirits_stage"),
        sa.CheckConstraint("status IN ('home', 'away', 'study', 'lost')", name="ck_spirits_status"),
        sa.CheckConstraint("invite_code ~ '^[A-HJ-NP-Z2-9]{8}$'", name="ck_spirits_invite_code"),
        sa.CheckConstraint(
            "closeness BETWEEN 0 AND 100 AND curiosity BETWEEN 0 AND 100 "
            "AND sharpness BETWEEN 0 AND 100 AND nocturnal BETWEEN 0 AND 100 "
            "AND stubborn BETWEEN 0 AND 100 AND hunger BETWEEN 0 AND 100 "
            "AND energy BETWEEN 0 AND 100 AND mood BETWEEN 0 AND 100 "
            "AND bond BETWEEN 0 AND 100",
            name="ck_spirits_trait_ranges",
        ),
        sa.CheckConstraint("onboarding_step BETWEEN 0 AND 5", name="ck_spirits_onboarding_step"),
        sa.CheckConstraint("ordinary_dialogue_rounds >= 0", name="ck_spirits_ordinary_rounds"),
        sa.CheckConstraint(
            "(onboarding_completed_at IS NULL) OR (onboarding_step = 5 AND hatched_at IS NOT NULL)",
            name="ck_spirits_onboarding_complete",
        ),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.set_updated_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          NEW.updated_at = now();
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER user_preferences_set_updated_at
        BEFORE UPDATE ON public.user_preferences
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )
    op.execute(
        """
        CREATE TRIGGER spirits_set_updated_at
        BEFORE UPDATE ON public.spirits
        FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS spirits_set_updated_at ON public.spirits")
    op.execute("DROP TRIGGER IF EXISTS user_preferences_set_updated_at ON public.user_preferences")
    op.execute("DROP FUNCTION IF EXISTS public.set_updated_at()")
    op.drop_table("spirits")
    op.drop_table("user_preferences")
    op.drop_table("account_consents")
    op.drop_table("users", schema="auth")
    op.execute("DROP SCHEMA IF EXISTS auth")
