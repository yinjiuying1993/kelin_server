"""Detail of a visit card names the other spirit, matching the footprint row.

Revision ID: 20261022_0115
Revises: 20261022_0114
Create Date: 2026-10-22
"""

from collections.abc import Sequence
import importlib.util
from pathlib import Path

from alembic import op

revision: str = "20261022_0115"
down_revision: str | None = "20261022_0114"
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
    previous = _load("20261022_0114_visit_card_occurred_at.py")
    sql = str(previous._read_sql())
    sql = sql.replace(
        "  occurred_at timestamptz\n)",
        "  occurred_at timestamptz,\n  peer_title text,\n  peer_stage text\n)",
        1,
    )
    old_tail = "  END IF;\n  RETURN NEXT;"
    new_tail = """  END IF;
  IF caller = sealed_host THEN
    SELECT
      COALESCE(NULLIF(btrim(visitor.name), ''), '未名'),
      CASE
        WHEN visitor.stage IN ('whelp', 'formed', 'awake') THEN visitor.stage
        ELSE 'formed'
      END
    INTO peer_title, peer_stage
    FROM public.spirits AS visitor
    WHERE visitor.id = sealed_visitor;
  ELSE
    SELECT
      COALESCE(host.name, NULLIF(v.public_context->>'title', ''), '未名'),
      CASE
        WHEN host.stage IN ('whelp', 'formed', 'awake') THEN host.stage
        WHEN v.public_context->>'stage' IN ('whelp', 'formed', 'awake') THEN v.public_context->>'stage'
        ELSE 'formed'
      END
    INTO peer_title, peer_stage
    FROM public.visit_cards AS c
    JOIN public.visits AS v ON v.id = c.visit_id
    LEFT JOIN public.spirits AS host ON host.id = c.host_spirit_id
    WHERE c.id = sealed_id;
  END IF;
  peer_title := COALESCE(peer_title, '未名');
  peer_stage := COALESCE(peer_stage, 'formed');
  RETURN NEXT;"""
    if sql.count(old_tail) != 1:
        raise RuntimeError("visit card read tail is missing")
    sql = sql.replace(old_tail, new_tail, 1)
    if "peer_title text" not in sql:
        raise RuntimeError("visit card read is not returning the other spirit")
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
    previous = _load("20261022_0114_visit_card_occurred_at.py")
    op.execute("DROP FUNCTION IF EXISTS private.read_visit_card(uuid)")
    op.execute(str(previous._read_sql()))
    _grant()
