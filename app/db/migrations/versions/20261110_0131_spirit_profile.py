"""Nickname and avatar keys on the owner's spirit.

Revision ID: 20261110_0131
Revises: 20261109_0130
Create Date: 2026-11-10

Existing rows receive the first default key. New creates pick from the set.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261110_0131"
down_revision: str | None = "20261109_0130"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "public.spirits"


def upgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN nickname text")
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT ck_spirits_nickname "
        "CHECK (nickname IS NULL OR char_length(nickname) BETWEEN 1 AND 12)"
    )
    op.execute(
        f"ALTER TABLE {_TABLE} ADD COLUMN default_avatar_key text "
        "NOT NULL DEFAULT 'spirit-avatar/default-1'"
    )
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT ck_spirits_default_avatar_key "
        "CHECK (char_length(default_avatar_key) BETWEEN 1 AND 200)"
    )
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN custom_avatar_key text")
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT ck_spirits_custom_avatar_key "
        "CHECK (custom_avatar_key IS NULL OR char_length(custom_avatar_key) BETWEEN 1 AND 200)"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS ck_spirits_custom_avatar_key")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS custom_avatar_key")
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS ck_spirits_default_avatar_key")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS default_avatar_key")
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS ck_spirits_nickname")
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS nickname")
