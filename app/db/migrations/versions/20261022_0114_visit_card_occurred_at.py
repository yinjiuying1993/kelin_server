"""Detail of a visit card includes the same clock as its footprint row.

Revision ID: 20261022_0114
Revises: 20261022_0113
Create Date: 2026-10-22
"""

from collections.abc import Sequence
import importlib.util
from pathlib import Path

from alembic import op

revision: str = "20261022_0114"
down_revision: str | None = "20261022_0113"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"
_READER = "kelin_api"


def _load(name: str) -> object:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"migration source is missing: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_sql() -> str:
    previous = _load("20261022_0113_visit_card_unread.py")
    sql = str(previous._read_sql())
    sql = sql.replace(
        "  unread boolean\n)",
        "  unread boolean,\n  occurred_at timestamptz\n)",
        1,
    )
    old_tail = "  END IF;\n  RETURN NEXT;"
    new_tail = """  END IF;
  IF caller = sealed_host THEN
    SELECT c.created_at INTO occurred_at
    FROM public.visit_cards AS c
    WHERE c.id = sealed_id;
  ELSE
    SELECT COALESCE(v.settled_at, c.created_at) INTO occurred_at
    FROM public.visit_cards AS c
    LEFT JOIN public.visits AS v ON v.id = c.visit_id
    WHERE c.id = sealed_id;
  END IF;
  RETURN NEXT;"""
    if sql.count(old_tail) != 1:
        raise RuntimeError("visit card read tail is missing")
    sql = sql.replace(old_tail, new_tail, 1)
    if "occurred_at timestamptz" not in sql:
        raise RuntimeError("visit card read is not returning occurred_at")
    return sql


def _grant() -> None:
    op.execute(f"ALTER FUNCTION private.read_visit_card(uuid) OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION private.read_visit_card(uuid) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION private.read_visit_card(uuid) TO {_READER}")


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.read_visit_card(uuid)")
    op.execute(_read_sql())
    _grant()


def downgrade() -> None:
    previous = _load("20261022_0113_visit_card_unread.py")
    op.execute("DROP FUNCTION IF EXISTS private.read_visit_card(uuid)")
    op.execute(str(previous._read_sql()))
    _grant()
