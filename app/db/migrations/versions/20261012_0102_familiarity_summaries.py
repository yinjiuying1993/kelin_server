"""Familiarity summaries on spirit_growth. S6-T04.

Revision ID: 20261012_0102
Revises: 20260920_0101
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261012_0102"
down_revision: str | None = "20260920_0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "spirit_growth",
        sa.Column(
            "familiarity_summaries",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_spirit_growth_familiarity_summaries",
        "spirit_growth",
        "jsonb_typeof(familiarity_summaries) = 'array'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_spirit_growth_familiarity_summaries",
        "spirit_growth",
        type_="check",
    )
    op.drop_column("spirit_growth", "familiarity_summaries")
