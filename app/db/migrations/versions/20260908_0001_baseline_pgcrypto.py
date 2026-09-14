"""Enable pgcrypto for gen_random_uuid. No V0 business tables.

Revision ID: 20260908_0001
Revises:
Create Date: 2026-09-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
