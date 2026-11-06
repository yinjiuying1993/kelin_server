"""Craft skills, match index, and self-test tickets.

Revision ID: 20261103_0122
Revises: 20261023_0121
Create Date: 2026-11-03

Skill rows are soft-deleted. The API and worker roles have no DELETE grant.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.db.rls_matrix import (
    RLS_MATRIX_V3,
    enable_force_statements,
    grant_statements,
    policy_create_statements,
)
from sqlalchemy.dialects import postgresql

revision: str = "20261103_0122"
down_revision: str | None = "20261023_0121"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("craft_skills", "craft_match_index", "craft_self_test_tickets")

_SLUG = "skill_slug ~ '^[a-z0-9-]{1,64}$'"
_RANK = (
    "(points = 0 AND rank_name IS NULL) OR "
    "(points BETWEEN 1 AND 99 AND rank_name = '门边学徒') OR "
    "(points BETWEEN 100 AND 299 AND rank_name = '搭手帮工') OR "
    "(points BETWEEN 300 AND 599 AND rank_name = '上手匠人') OR "
    "(points BETWEEN 600 AND 999 AND rank_name = '独当师傅') OR "
    "(points BETWEEN 1000 AND 1499 AND rank_name = '掌作名匠') OR "
    "(points >= 1500 AND rank_name = '镇场宗师')"
)
_DRAFT = (
    "(draft_title IS NULL AND draft_keywords IS NULL AND draft_detail IS NULL "
    "AND draft_reference_links IS NULL AND draft_body IS NULL) OR "
    "(draft_title IS NOT NULL AND draft_keywords IS NOT NULL AND draft_detail IS NOT NULL "
    "AND draft_reference_links IS NOT NULL AND draft_body IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        "craft_skills",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("keywords", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "reference_links",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("draft_title", sa.Text(), nullable=True),
        sa.Column("draft_keywords", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("draft_detail", sa.Text(), nullable=True),
        sa.Column("draft_reference_links", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("draft_body", sa.Text(), nullable=True),
        sa.Column(
            "visibility",
            sa.Text(),
            server_default=sa.text("'unpublished'"),
            nullable=False,
        ),
        sa.Column("points", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rank_name", sa.Text(), nullable=True),
        sa.Column(
            "content_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_craft_skills"),
        sa.ForeignKeyConstraint(
            ["spirit_id"],
            ["spirits.id"],
            name="fk_craft_skills_spirit_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("skill_slug", name="uq_craft_skills_skill_slug"),
        sa.CheckConstraint(_SLUG, name="ck_craft_skills_skill_slug"),
        sa.CheckConstraint(
            "visibility IN ('unpublished', 'public', 'private')",
            name="ck_craft_skills_visibility",
        ),
        sa.CheckConstraint("points >= 0", name="ck_craft_skills_points"),
        sa.CheckConstraint(_RANK, name="ck_craft_skills_rank"),
        sa.CheckConstraint("cardinality(keywords) >= 1", name="ck_craft_skills_keywords"),
        sa.CheckConstraint("char_length(title) >= 1", name="ck_craft_skills_title"),
        sa.CheckConstraint("char_length(detail) >= 1", name="ck_craft_skills_detail"),
        sa.CheckConstraint("char_length(body) >= 1", name="ck_craft_skills_body"),
        sa.CheckConstraint(_DRAFT, name="ck_craft_skills_draft"),
        schema="public",
    )
    op.create_index(
        "craft_skills_owner_recent",
        "craft_skills",
        ["spirit_id", "content_updated_at"],
        schema="public",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "craft_match_index",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("craft_skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_slug", sa.Text(), nullable=False),
        sa.Column("active_title", sa.Text(), nullable=True),
        sa.Column("active_keywords", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("staged_title", sa.Text(), nullable=True),
        sa.Column("staged_keywords", postgresql.ARRAY(sa.Text()), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_craft_match_index"),
        sa.ForeignKeyConstraint(
            ["spirit_id"],
            ["spirits.id"],
            name="fk_craft_match_index_spirit_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["craft_skill_id"],
            ["craft_skills.id"],
            name="fk_craft_match_index_craft_skill_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("craft_skill_id", name="uq_craft_match_index_skill"),
        sa.UniqueConstraint("skill_slug", name="uq_craft_match_index_skill_slug"),
        sa.CheckConstraint(_SLUG, name="ck_craft_match_index_skill_slug"),
        sa.CheckConstraint(
            "(active_title IS NULL AND active_keywords IS NULL) OR "
            "(active_title IS NOT NULL AND active_keywords IS NOT NULL)",
            name="ck_craft_match_index_active_pair",
        ),
        sa.CheckConstraint(
            "(staged_title IS NULL AND staged_keywords IS NULL) OR "
            "(staged_title IS NOT NULL AND staged_keywords IS NOT NULL)",
            name="ck_craft_match_index_staged_pair",
        ),
        sa.CheckConstraint(
            "active_title IS NOT NULL OR staged_title IS NOT NULL",
            name="ck_craft_match_index_present",
        ),
        schema="public",
    )
    op.create_index(
        "craft_match_index_active",
        "craft_match_index",
        ["spirit_id"],
        schema="public",
        postgresql_where=sa.text("active_title IS NOT NULL"),
    )
    op.create_table(
        "craft_self_test_tickets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("craft_skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_slug", sa.Text(), nullable=False),
        sa.Column("snapshot_body", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_craft_self_test_tickets"),
        sa.ForeignKeyConstraint(
            ["spirit_id"],
            ["spirits.id"],
            name="fk_craft_self_test_tickets_spirit_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["craft_skill_id"],
            ["craft_skills.id"],
            name="fk_craft_self_test_tickets_craft_skill_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_SLUG, name="ck_craft_self_test_tickets_skill_slug"),
        sa.CheckConstraint(
            "char_length(snapshot_body) >= 1",
            name="ck_craft_self_test_tickets_snapshot_body",
        ),
        schema="public",
    )
    op.create_index(
        "craft_self_test_tickets_expires",
        "craft_self_test_tickets",
        ["expires_at"],
        schema="public",
    )
    rows = tuple(row for row in RLS_MATRIX_V3 if row.table in _TABLES)
    for statement in enable_force_statements(rows):
        op.execute(statement)
    for statement in policy_create_statements(rows):
        op.execute(statement)
    for statement in grant_statements(rows):
        op.execute(statement)


def downgrade() -> None:
    op.drop_index("craft_self_test_tickets_expires", table_name="craft_self_test_tickets")
    op.drop_table("craft_self_test_tickets")
    op.drop_index("craft_match_index_active", table_name="craft_match_index")
    op.drop_table("craft_match_index")
    op.drop_index("craft_skills_owner_recent", table_name="craft_skills")
    op.drop_table("craft_skills")
