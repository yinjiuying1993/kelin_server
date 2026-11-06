"""Ask dialogues, turns, and one rating per dialogue.

Revision ID: 20261103_0123
Revises: 20261103_0122
Create Date: 2026-11-03

The skill snapshot is stored on the dialogue. It does not reference craft_skills.
"""

from collections.abc import Sequence

from alembic import op
from app.db.rls_matrix import (
    RLS_MATRIX_V3,
    RlsTableExpectation,
    enable_force_statements,
    grant_statements,
    policy_create_statements,
)
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql

revision: str = "20261103_0123"
down_revision: str | None = "20261103_0122"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ASK_TABLES = ("ask_questions", "ask_turns", "ask_ratings")
_SLUG = "snapshot_skill_slug ~ '^[a-z0-9-]{1,64}$'"
_RANK = (
    "snapshot_rank_name IS NULL OR snapshot_rank_name IN ("
    "'门边学徒', '搭手帮工', '上手匠人', '独当师傅', '掌作名匠', '镇场宗师')"
)
_RATING = (
    "(rating = '不满意' AND points = 0) OR "
    "(rating = '满意' AND points = 5) OR "
    "(rating = '非常满意' AND points = 10)"
)
_TURN_REPLY = (
    "(status = 'succeeded' AND reply_text IS NOT NULL AND char_length(reply_text) >= 1) OR "
    "(status IN ('pending', 'failed') AND reply_text IS NULL)"
)


def _v3_ask_rows() -> tuple[RlsTableExpectation, ...]:
    return tuple(row for row in RLS_MATRIX_V3 if row.table in _ASK_TABLES)


def upgrade() -> None:
    op.create_table(
        "ask_questions",
        Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=text("gen_random_uuid()"),
            nullable=False,
        ),
        Column("asker_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("spirit_owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("question_text", Text(), nullable=False),
        Column("snapshot_title", Text(), nullable=False),
        Column("snapshot_rank_name", Text(), nullable=True),
        Column("snapshot_skill_slug", Text(), nullable=False),
        Column("snapshot_body", Text(), nullable=False),
        Column("snapshot_detail", Text(), nullable=False),
        Column(
            "snapshot_reference_links",
            postgresql.ARRAY(Text()),
            server_default=text("'{}'::text[]"),
            nullable=False,
        ),
        Column("first_reply_at", DateTime(timezone=True), nullable=True),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint("id", name="pk_ask_questions"),
        ForeignKeyConstraint(
            ["asker_user_id"],
            ["auth.users.id"],
            name="fk_ask_questions_asker_user_id",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "asker_user_id", name="uq_ask_questions_id_asker"),
        UniqueConstraint("asker_user_id", "client_id", name="uq_ask_questions_asker_client"),
        CheckConstraint(
            "asker_user_id <> spirit_owner_user_id",
            name="ck_ask_questions_not_own_spirit",
        ),
        CheckConstraint(
            "char_length(question_text) BETWEEN 1 AND 500",
            name="ck_ask_questions_question_text",
        ),
        CheckConstraint(_SLUG, name="ck_ask_questions_skill_slug"),
        CheckConstraint(_RANK, name="ck_ask_questions_rank_name"),
        CheckConstraint("char_length(snapshot_title) >= 1", name="ck_ask_questions_snapshot_title"),
        CheckConstraint(
            "char_length(snapshot_detail) >= 1",
            name="ck_ask_questions_snapshot_detail",
        ),
        CheckConstraint("char_length(snapshot_body) >= 1", name="ck_ask_questions_snapshot_body"),
        schema="public",
    )
    op.create_index(
        "ask_questions_history",
        "ask_questions",
        ["asker_user_id", "first_reply_at"],
        schema="public",
        postgresql_where=text("first_reply_at IS NOT NULL"),
    )
    op.create_table(
        "ask_turns",
        Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=text("gen_random_uuid()"),
            nullable=False,
        ),
        Column("ask_question_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("asker_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("user_text", Text(), nullable=False),
        Column("reply_text", Text(), nullable=True),
        Column("status", Text(), server_default=text("'pending'"), nullable=False),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint("id", name="pk_ask_turns"),
        ForeignKeyConstraint(
            ["ask_question_id", "asker_user_id"],
            ["public.ask_questions.id", "public.ask_questions.asker_user_id"],
            name="fk_ask_turns_question_asker",
            ondelete="CASCADE",
        ),
        UniqueConstraint("ask_question_id", "client_id", name="uq_ask_turns_question_client"),
        CheckConstraint(
            "char_length(user_text) BETWEEN 1 AND 500",
            name="ck_ask_turns_user_text",
        ),
        CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="ck_ask_turns_status"),
        CheckConstraint(_TURN_REPLY, name="ck_ask_turns_reply"),
        schema="public",
    )
    op.create_table(
        "ask_ratings",
        Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=text("gen_random_uuid()"),
            nullable=False,
        ),
        Column("ask_question_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("asker_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("rating", Text(), nullable=False),
        Column("points", Integer(), nullable=False),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint("id", name="pk_ask_ratings"),
        ForeignKeyConstraint(
            ["ask_question_id", "asker_user_id"],
            ["public.ask_questions.id", "public.ask_questions.asker_user_id"],
            name="fk_ask_ratings_question_asker",
            ondelete="CASCADE",
        ),
        UniqueConstraint("ask_question_id", name="uq_ask_ratings_question"),
        CheckConstraint(_RATING, name="ck_ask_ratings_points"),
        schema="public",
    )
    ask_rows = _v3_ask_rows()
    for statement in enable_force_statements(ask_rows):
        op.execute(statement)
    for statement in policy_create_statements(ask_rows):
        op.execute(statement)
    for statement in grant_statements(ask_rows):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("ask_ratings")
    op.drop_table("ask_turns")
    op.drop_index("ask_questions_history", table_name="ask_questions")
    op.drop_table("ask_questions")
