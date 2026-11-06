"""Drop personality, mood, and bond. They are no longer read.

Revision ID: 20261023_0119
Revises: 20261023_0118
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_0119"
down_revision: str | None = "20261023_0118"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRAIT_COLUMNS = (
    "closeness",
    "curiosity",
    "sharpness",
    "nocturnal",
    "stubborn",
    "mood",
    "bond",
)
_TRAIT_RANGES = (
    "closeness BETWEEN 0 AND 100 AND curiosity BETWEEN 0 AND 100 "
    "AND sharpness BETWEEN 0 AND 100 AND nocturnal BETWEEN 0 AND 100 "
    "AND stubborn BETWEEN 0 AND 100 AND mood BETWEEN 0 AND 100 "
    "AND bond BETWEEN 0 AND 100"
)


def upgrade() -> None:
    op.drop_constraint("ck_spirits_trait_ranges", "spirits", type_="check")
    for name in _TRAIT_COLUMNS:
        op.drop_column("spirits", name)


def downgrade() -> None:
    defaults = {
        "closeness": "50",
        "curiosity": "50",
        "sharpness": "50",
        "nocturnal": "50",
        "stubborn": "50",
        "mood": "60",
        "bond": "0",
    }
    for name in _TRAIT_COLUMNS:
        op.add_column(
            "spirits",
            sa.Column(
                name,
                sa.SmallInteger(),
                server_default=sa.text(defaults[name]),
                nullable=False,
            ),
        )
    op.create_check_constraint("ck_spirits_trait_ranges", "spirits", _TRAIT_RANGES)
