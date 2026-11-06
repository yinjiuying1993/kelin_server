"""Drop hunger and energy. Status no longer reads them.

Revision ID: 20261023_0117
Revises: 20261023_0116
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_0117"
down_revision: str | None = "20261023_0116"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRAITS_WITH_LIFE = (
    "closeness BETWEEN 0 AND 100 AND curiosity BETWEEN 0 AND 100 "
    "AND sharpness BETWEEN 0 AND 100 AND nocturnal BETWEEN 0 AND 100 "
    "AND stubborn BETWEEN 0 AND 100 AND hunger BETWEEN 0 AND 100 "
    "AND energy BETWEEN 0 AND 100 AND mood BETWEEN 0 AND 100 "
    "AND bond BETWEEN 0 AND 100"
)
_TRAITS_WITHOUT_LIFE = (
    "closeness BETWEEN 0 AND 100 AND curiosity BETWEEN 0 AND 100 "
    "AND sharpness BETWEEN 0 AND 100 AND nocturnal BETWEEN 0 AND 100 "
    "AND stubborn BETWEEN 0 AND 100 AND mood BETWEEN 0 AND 100 "
    "AND bond BETWEEN 0 AND 100"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_spirit_awareness_hunger_energy", "spirit_awareness", type_="check"
    )
    op.drop_column("spirit_awareness", "hunger")
    op.drop_column("spirit_awareness", "energy")
    op.drop_constraint("ck_spirits_trait_ranges", "spirits", type_="check")
    op.drop_column("spirits", "hunger")
    op.drop_column("spirits", "energy")
    op.create_check_constraint(
        "ck_spirits_trait_ranges", "spirits", _TRAITS_WITHOUT_LIFE
    )


def downgrade() -> None:
    op.drop_constraint("ck_spirits_trait_ranges", "spirits", type_="check")
    op.add_column(
        "spirits",
        sa.Column("hunger", sa.SmallInteger(), server_default=sa.text("80"), nullable=False),
    )
    op.add_column(
        "spirits",
        sa.Column("energy", sa.SmallInteger(), server_default=sa.text("80"), nullable=False),
    )
    op.create_check_constraint("ck_spirits_trait_ranges", "spirits", _TRAITS_WITH_LIFE)
    op.add_column(
        "spirit_awareness",
        sa.Column("hunger", sa.SmallInteger(), server_default=sa.text("80"), nullable=False),
    )
    op.add_column(
        "spirit_awareness",
        sa.Column("energy", sa.SmallInteger(), server_default=sa.text("80"), nullable=False),
    )
    op.create_check_constraint(
        "ck_spirit_awareness_hunger_energy",
        "spirit_awareness",
        "hunger BETWEEN 0 AND 100 AND energy BETWEEN 0 AND 100",
    )
