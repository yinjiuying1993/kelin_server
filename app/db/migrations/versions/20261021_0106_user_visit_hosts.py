"""Visits go only to user spirits. Official catalog rows are removed.

Revision ID: 20261021_0106
Revises: 20261020_0105
Create Date: 2026-10-21
"""

from collections.abc import Sequence
import importlib.util
from pathlib import Path

from alembic import op

revision: str = "20261021_0106"
down_revision: str | None = "20261020_0105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _previous_plan_sql() -> str:
    path = Path(__file__).with_name("20261020_0104_visit_cards.py")
    spec = importlib.util.spec_from_file_location("visit_cards_0104", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("visit plan source is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module._PLAN_VISITS_SQL)


def _host_only_plan_sql() -> str:
    sql = _previous_plan_sql()
    sql = sql.replace("          npc_dests jsonb;\n", "")
    start = sql.index("            SELECT COALESCE(jsonb_agg(picked.dest_row")
    end = sql.index("            IF jsonb_array_length(dests) < 1 THEN")
    sql = sql[:start] + "            dests := COALESCE(host_dests, '[]'::jsonb);\n\n" + sql[end:]
    if "npc_profiles" in sql or "npc_dests" in sql:
        raise RuntimeError("visit plan still selects official spirits")
    return sql


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM public.outbox_events AS event
        USING public.visits AS visit
        WHERE event.event_type = 'visit.settle'
          AND event.aggregate_id = visit.id
          AND visit.npc_id IS NOT NULL
        """
    )
    op.execute("DELETE FROM public.visits WHERE npc_id IS NOT NULL")
    op.execute(
        "DELETE FROM public.npc_profiles WHERE npc_id IN ('fog', 'lamp', 'silent')"
    )
    op.execute(_host_only_plan_sql())


def downgrade() -> None:
    op.execute(_previous_plan_sql())
    op.execute(
        """
        INSERT INTO public.npc_profiles (
          npc_id, config_version, display_name, title, template_key, enabled
        ) VALUES
          ('fog', 1, '雾里的那只', '雾里的那只', 'npc_fog_v1', true),
          ('lamp', 1, '守灯的那只', '守灯的那只', 'npc_lamp_v1', true),
          ('silent', 1, '不说话的那只', '不说话的那只', 'npc_silent_v1', true)
        ON CONFLICT (npc_id, config_version) DO NOTHING
        """
    )
