"""Let the worker name the expiry column when deleting old tickets.

Revision ID: 20261105_0129
Revises: 20261104_0128
Create Date: 2026-11-05

DELETE WHERE expires_at needs SELECT on that column. The worker still cannot
read the ticket body.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261105_0129"
down_revision: str | None = "20261104_0128"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "public.craft_self_test_tickets"


def upgrade() -> None:
    op.execute(f"GRANT SELECT (expires_at) ON TABLE {_TABLE} TO kelin_worker")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT (expires_at) ON TABLE {_TABLE} FROM kelin_worker")
