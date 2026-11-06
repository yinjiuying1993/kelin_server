"""Craft ledger and one settlement row per ask dialogue.

Revision ID: 20261103_0124
Revises: 20261103_0123
Create Date: 2026-11-03

Neither table references growth, feed, or bond tables.
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
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql

revision: str = "20261103_0124"
down_revision: str | None = "20261103_0123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("craft_ledgers", "craft_settlements")


def _rows() -> tuple[RlsTableExpectation, ...]:
    return tuple(row for row in RLS_MATRIX_V3 if row.table in _TABLES)


def upgrade() -> None:
    op.create_table(
        "craft_ledgers",
        Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=text("gen_random_uuid()"),
            nullable=False,
        ),
        Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("total_points", Integer(), server_default=text("0"), nullable=False),
        Column("help_count", Integer(), server_default=text("0"), nullable=False),
        Column("redeemed_at", DateTime(timezone=True), nullable=True),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        Column(
            "updated_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint("id", name="pk_craft_ledgers"),
        ForeignKeyConstraint(
            ["spirit_id"],
            ["public.spirits.id"],
            name="fk_craft_ledgers_spirit_id",
            ondelete="CASCADE",
        ),
        UniqueConstraint("spirit_id", name="uq_craft_ledgers_spirit"),
        CheckConstraint("total_points >= 0", name="ck_craft_ledgers_total_points"),
        CheckConstraint("help_count >= 0", name="ck_craft_ledgers_help_count"),
        schema="public",
    )
    op.create_table(
        "craft_settlements",
        Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=text("gen_random_uuid()"),
            nullable=False,
        ),
        Column("ask_question_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("points_added", Integer(), nullable=False),
        Column("help_counted", Boolean(), server_default=text("true"), nullable=False),
        Column("skill_points_applied", Boolean(), nullable=False),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint("id", name="pk_craft_settlements"),
        ForeignKeyConstraint(
            ["ask_question_id"],
            ["public.ask_questions.id"],
            name="fk_craft_settlements_ask_question_id",
            ondelete="CASCADE",
        ),
        UniqueConstraint("ask_question_id", name="uq_craft_settlements_question"),
        CheckConstraint("points_added IN (0, 5, 10)", name="ck_craft_settlements_points"),
        CheckConstraint("help_counted", name="ck_craft_settlements_help_counted"),
        CheckConstraint(
            "skill_points_applied = false OR points_added > 0",
            name="ck_craft_settlements_skill_points",
        ),
        schema="public",
    )
    rows = _rows()
    for statement in enable_force_statements(rows):
        op.execute(statement)
    for statement in policy_create_statements(rows):
        op.execute(statement)
    for statement in grant_statements(rows):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("craft_settlements")
    op.drop_table("craft_ledgers")
