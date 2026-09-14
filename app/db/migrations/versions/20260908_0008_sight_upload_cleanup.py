"""Track sight original deletion. Spec §§11.4, 18.5.

Revision ID: 20260908_0008
Revises: 20260908_0007
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0008"
down_revision: str | None = "20260908_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sight_uploads",
        sa.Column("object_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sight_uploads", "object_deleted_at")
