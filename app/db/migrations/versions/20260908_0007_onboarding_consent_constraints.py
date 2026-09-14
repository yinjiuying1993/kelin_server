"""Tighten consent assertion, preference defaults, and spirit version.

Revision ID: 20260908_0007
Revises: 20260908_0006
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0007"
down_revision: str | None = "20260908_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("account_consents", sa.Column("assertion", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE public.account_consents
        SET assertion = CASE
          WHEN consent_type = 'ai_disclosure' THEN 'explicitly_accepted'
          ELSE 'displayed'
        END
        WHERE assertion IS NULL
        """
    )
    op.alter_column(
        "account_consents",
        "assertion",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_account_consents_assertion",
        "account_consents",
        "assertion IN ('explicitly_accepted', 'displayed')",
    )
    op.create_check_constraint(
        "ck_account_consents_assertion_matches_type",
        "account_consents",
        "(consent_type = 'ai_disclosure' AND assertion = 'explicitly_accepted') OR "
        "(consent_type IN ('user_terms', 'data_notice') AND assertion = 'displayed')",
    )
    op.create_check_constraint(
        "ck_account_consents_ai_disclosure_not_withdrawn",
        "account_consents",
        "consent_type <> 'ai_disclosure' OR withdrawn_at IS NULL",
    )
    op.create_check_constraint(
        "ck_user_preferences_timezone_len",
        "user_preferences",
        "char_length(timezone) BETWEEN 1 AND 64",
    )
    op.create_check_constraint(
        "ck_user_preferences_default_city_len",
        "user_preferences",
        "default_city IS NULL OR char_length(default_city) BETWEEN 1 AND 40",
    )
    op.create_check_constraint(
        "ck_user_preferences_version_positive",
        "user_preferences",
        "version >= 1",
    )
    op.alter_column(
        "spirits",
        "version",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        existing_server_default=sa.text("1"),
    )


def downgrade() -> None:
    op.alter_column(
        "spirits",
        "version",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        existing_server_default=sa.text("1"),
    )
    op.drop_constraint("ck_user_preferences_version_positive", "user_preferences", type_="check")
    op.drop_constraint("ck_user_preferences_default_city_len", "user_preferences", type_="check")
    op.drop_constraint("ck_user_preferences_timezone_len", "user_preferences", type_="check")
    op.drop_constraint(
        "ck_account_consents_ai_disclosure_not_withdrawn",
        "account_consents",
        type_="check",
    )
    op.drop_constraint(
        "ck_account_consents_assertion_matches_type",
        "account_consents",
        type_="check",
    )
    op.drop_constraint("ck_account_consents_assertion", "account_consents", type_="check")
    op.drop_column("account_consents", "assertion")
