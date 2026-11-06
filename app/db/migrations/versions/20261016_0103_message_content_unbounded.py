"""Drop the 4000-character cap on chat messages.

Revision ID: 20261016_0103
Revises: 20261012_0102
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261016_0103"
down_revision: str | None = "20261012_0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_messages_content_length", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_content_length",
        "messages",
        "char_length(content) >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_messages_content_length", "messages", type_="check")
    op.create_check_constraint(
        "ck_messages_content_length",
        "messages",
        "char_length(content) BETWEEN 1 AND 4000",
    )
