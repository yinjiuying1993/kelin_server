"""Visit hosts are a random pair of other user spirits.

Revision ID: 20261021_0107
Revises: 20261021_0106
Create Date: 2026-10-21
"""

from collections.abc import Sequence
import importlib.util
from pathlib import Path

from alembic import op

revision: str = "20261021_0107"
down_revision: str | None = "20261021_0106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RANDOM_HOSTS = """row_number() OVER () AS ord
              FROM (
                SELECT peer.id, peer.name, peer.stage, peer.scholar_marks
                FROM public.spirits peer
                LEFT JOIN public.user_preferences hp ON hp.user_id = peer.user_id
                WHERE peer.id <> visitor.spirit_id
                  AND peer.status <> 'lost'
                  AND COALESCE(hp.visit_on, true) IS TRUE
                ORDER BY random()
                LIMIT 2
              ) peer"""


def _load(name: str) -> object:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"migration source is missing: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _random_plan_sql() -> str:
    previous = _load("20261021_0106_user_visit_hosts.py")
    sql = str(previous._host_only_plan_sql())
    start = sql.index("row_number() OVER (ORDER BY")
    end = sql.index("LIMIT 2") + len("LIMIT 2")
    sql = sql[:start] + _RANDOM_HOSTS + sql[end:]
    if "public.friends" in sql or "ORDER BY random()" not in sql:
        raise RuntimeError("visit plan is not choosing random user spirits")
    return sql


def upgrade() -> None:
    op.execute(_random_plan_sql())
    op.execute("SELECT private.plan_visits(clock_timestamp())")


def downgrade() -> None:
    previous = _load("20261021_0106_user_visit_hosts.py")
    op.execute(str(previous._host_only_plan_sql()))
