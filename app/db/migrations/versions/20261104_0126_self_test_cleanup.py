"""Let the worker delete expired self-test tickets without reading them.

Revision ID: 20261104_0126
Revises: 20261104_0125
Create Date: 2026-11-04

The API role still cannot delete a ticket.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261104_0126"
down_revision: str | None = "20261104_0125"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "public.craft_self_test_tickets"
_OWNER = (
    "EXISTS (SELECT 1 FROM public.spirits s "
    "WHERE s.id = spirit_id AND s.user_id = auth.uid())"
)


def upgrade() -> None:
    op.execute(f"REVOKE SELECT ON TABLE {_TABLE} FROM kelin_worker")
    op.execute(
        "DROP POLICY IF EXISTS rls_craft_self_test_tickets_kelin_worker_select "
        f"ON {_TABLE}"
    )
    op.execute(
        "DROP POLICY IF EXISTS rls_craft_self_test_tickets_kelin_worker_delete "
        f"ON {_TABLE}"
    )
    op.execute(
        "CREATE POLICY rls_craft_self_test_tickets_kelin_worker_delete "
        f"ON {_TABLE} AS PERMISSIVE FOR DELETE TO kelin_worker USING (true)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS rls_craft_self_test_tickets_kelin_worker_delete "
        f"ON {_TABLE}"
    )
    op.execute(f"GRANT SELECT ON TABLE {_TABLE} TO kelin_worker")
    op.execute(
        "CREATE POLICY rls_craft_self_test_tickets_kelin_worker_select "
        f"ON {_TABLE} AS PERMISSIVE FOR SELECT TO kelin_worker USING ({_OWNER})"
    )
    op.execute(
        "CREATE POLICY rls_craft_self_test_tickets_kelin_worker_delete "
        f"ON {_TABLE} AS PERMISSIVE FOR DELETE TO kelin_worker USING ({_OWNER})"
    )
