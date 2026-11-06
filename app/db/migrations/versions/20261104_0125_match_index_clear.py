"""Clearing the last match-index slot is an update, not a delete.

Revision ID: 20261104_0125
Revises: 20261103_0124
Create Date: 2026-11-04

Questions read active_title only. The API role has no DELETE on this table.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261104_0125"
down_revision: str | None = "20261103_0124"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_craft_match_index_present",
        "craft_match_index",
        schema="public",
        type_="check",
    )


def downgrade() -> None:
    op.create_check_constraint(
        "ck_craft_match_index_present",
        "craft_match_index",
        "active_title IS NOT NULL OR staged_title IS NOT NULL",
        schema="public",
    )
