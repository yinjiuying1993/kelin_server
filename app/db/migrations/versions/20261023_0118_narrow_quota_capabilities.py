"""Keep chat, voice, and recall quotas. Drop unused feed and pact caps.

Revision ID: 20261023_0118
Revises: 20261023_0117
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261023_0118"
down_revision: str | None = "20261023_0117"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEPT = "capability IN ('chat', 'asr', 'tts', 'food')"
_DAILY_USAGE_OLD = (
    "capability IN ("
    "'chat', 'asr', 'tts', 'sight', 'search', 'food', "
    "'knowledge', 'emotion', 'visit', 'pact'"
    ")"
)
_ACTION_QUOTAS_OLD = (
    "capability IN ("
    "'chat', 'asr', 'tts', 'search', 'food', "
    "'knowledge', 'emotion', 'visit', 'pact', 'recall'"
    ")"
)
_RESERVATIONS_OLD = (
    "capability IN ("
    "'chat', 'asr', 'tts', 'sight', 'search', 'food', "
    "'knowledge', 'emotion', 'visit', 'pact'"
    ")"
)


def upgrade() -> None:
    op.execute(
        "DELETE FROM public.daily_usage "
        "WHERE capability NOT IN ('chat', 'asr', 'tts', 'food')"
    )
    op.execute(
        "DELETE FROM public.action_quotas "
        "WHERE capability NOT IN ('chat', 'asr', 'tts', 'food')"
    )
    op.execute(
        "DELETE FROM private.quota_reservations "
        "WHERE capability NOT IN ('chat', 'asr', 'tts', 'food')"
    )
    op.drop_constraint("ck_daily_usage_capability", "daily_usage", type_="check")
    op.create_check_constraint("ck_daily_usage_capability", "daily_usage", _KEPT)
    op.drop_constraint("ck_action_quotas_capability", "action_quotas", type_="check")
    op.create_check_constraint("ck_action_quotas_capability", "action_quotas", _KEPT)
    op.drop_constraint(
        "ck_quota_reservations_capability",
        "quota_reservations",
        schema="private",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quota_reservations_capability",
        "quota_reservations",
        _KEPT,
        schema="private",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_quota_reservations_capability",
        "quota_reservations",
        schema="private",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quota_reservations_capability",
        "quota_reservations",
        _RESERVATIONS_OLD,
        schema="private",
    )
    op.drop_constraint("ck_action_quotas_capability", "action_quotas", type_="check")
    op.create_check_constraint(
        "ck_action_quotas_capability", "action_quotas", _ACTION_QUOTAS_OLD
    )
    op.drop_constraint("ck_daily_usage_capability", "daily_usage", type_="check")
    op.create_check_constraint("ck_daily_usage_capability", "daily_usage", _DAILY_USAGE_OLD)
