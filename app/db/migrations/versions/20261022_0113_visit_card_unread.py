"""Detail of a visit card includes whether the caller still has it unread.

Revision ID: 20261022_0113
Revises: 20261021_0112
Create Date: 2026-10-22
"""

from collections.abc import Sequence
import importlib.util
from pathlib import Path

from alembic import op

revision: str = "20261022_0113"
down_revision: str | None = "20261021_0112"
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
    previous = _load("20261020_0105_visit_card_footprints.py")
    sql = str(previous._READ_SEALED_SQL)
    sql = sql.replace(
        "  reply_client_id uuid\n)",
        "  reply_client_id uuid,\n  unread boolean\n)",
        1,
    )
    sql = sql.replace(
        "  sealed_image_caption text;\n",
        "  sealed_image_caption text;\n  sealed_host_read timestamptz;\n",
        1,
    )
    sql = sql.replace(
        "    c.image_title, c.image_caption\n",
        "    c.image_title, c.image_caption, c.host_read_at\n",
        1,
    )
    sql = sql.replace(
        "    sealed_image_title, sealed_image_caption\n",
        "    sealed_image_title, sealed_image_caption, sealed_host_read\n",
        1,
    )
    old_tail = "    LIMIT 1;\n  END IF;\n  RETURN NEXT;"
    new_tail = """    LIMIT 1;
  END IF;
  IF caller = sealed_host THEN
    unread := sealed_host_read IS NULL;
  ELSE
    SELECT EXISTS (
      SELECT 1
      FROM public.visit_cards AS reply_card
      WHERE reply_card.role = 'reply'
        AND reply_card.parent_id = sealed_id
        AND reply_card.status = 'returned'
        AND reply_card.visitor_read_at IS NULL
    ) INTO unread;
  END IF;
  RETURN NEXT;"""
    if old_tail not in sql:
        raise RuntimeError("visit card read tail is missing")
    sql = sql.replace(old_tail, new_tail, 1)
    if "unread boolean" not in sql or "sealed_host_read" not in sql:
        raise RuntimeError("visit card read is not returning unread")
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
    previous = _load("20261020_0105_visit_card_footprints.py")
    op.execute("DROP FUNCTION IF EXISTS private.read_visit_card(uuid)")
    op.execute(str(previous._READ_SEALED_SQL))
    _grant()
