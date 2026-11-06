"""Status is only home or away. Drop the recall food quota.

Revision ID: 20261023_0121
Revises: 20261023_0120
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261023_0121"
down_revision: str | None = "20261023_0120"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS = "status IN ('home', 'away')"
_STATUS_OLD = "status IN ('home', 'away', 'study', 'lost')"
_QUOTA = "capability IN ('chat', 'asr', 'tts')"
_QUOTA_OLD = "capability IN ('chat', 'asr', 'tts', 'food')"


def upgrade() -> None:
    op.execute("UPDATE public.spirits SET status = 'away' WHERE status = 'study'")
    op.execute("UPDATE public.spirits SET status = 'home' WHERE status = 'lost'")
    op.execute("UPDATE public.spirit_awareness SET status = 'away' WHERE status = 'study'")
    op.execute("UPDATE public.spirit_awareness SET status = 'home' WHERE status = 'lost'")
    op.execute("UPDATE public.room_projections SET status = 'away' WHERE status = 'study'")
    op.execute("UPDATE public.room_projections SET status = 'home' WHERE status = 'lost'")
    op.execute(
        "UPDATE public.room_projections SET text_status = 'away' WHERE text_status = 'study'"
    )
    op.execute(
        "UPDATE public.room_projections SET text_status = 'home' WHERE text_status = 'lost'"
    )
    for table, name in (
        ("spirits", "ck_spirits_status"),
        ("spirit_awareness", "ck_spirit_awareness_status"),
        ("room_projections", "ck_room_projections_status"),
    ):
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} CHECK ({_STATUS})")
    op.execute("DELETE FROM public.daily_usage WHERE capability = 'food'")
    op.execute("DELETE FROM public.action_quotas WHERE capability = 'food'")
    op.execute("DELETE FROM private.quota_reservations WHERE capability = 'food'")
    op.drop_constraint("ck_daily_usage_capability", "daily_usage", type_="check")
    op.create_check_constraint("ck_daily_usage_capability", "daily_usage", _QUOTA)
    op.drop_constraint("ck_action_quotas_capability", "action_quotas", type_="check")
    op.create_check_constraint("ck_action_quotas_capability", "action_quotas", _QUOTA)
    op.drop_constraint(
        "ck_quota_reservations_capability",
        "quota_reservations",
        schema="private",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quota_reservations_capability",
        "quota_reservations",
        _QUOTA,
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
        _QUOTA_OLD,
        schema="private",
    )
    op.drop_constraint("ck_action_quotas_capability", "action_quotas", type_="check")
    op.create_check_constraint("ck_action_quotas_capability", "action_quotas", _QUOTA_OLD)
    op.drop_constraint("ck_daily_usage_capability", "daily_usage", type_="check")
    op.create_check_constraint("ck_daily_usage_capability", "daily_usage", _QUOTA_OLD)
    for table, name in (
        ("spirits", "ck_spirits_status"),
        ("spirit_awareness", "ck_spirit_awareness_status"),
        ("room_projections", "ck_room_projections_status"),
    ):
        op.execute(f"ALTER TABLE public.{table} DROP CONSTRAINT IF EXISTS {name}")
        op.execute(f"ALTER TABLE public.{table} ADD CONSTRAINT {name} CHECK ({_STATUS_OLD})")
