"""Spirits are only home or away. Clear leftover lost rows.

Revision ID: 20261023_0120
Revises: 20261023_0119
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261023_0120"
down_revision: str | None = "20261023_0119"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE public.spirit_awareness SET status = 'home' WHERE status = 'lost'")
    op.execute("UPDATE public.spirits SET status = 'home' WHERE status = 'lost'")
    op.execute(
        """
        UPDATE public.room_projections
        SET status = 'home', text_status = 'home', letter_key = NULL
        WHERE status = 'lost' OR text_status = 'lost'
        """
    )


def downgrade() -> None:
    pass
