"""Let the worker settle skill points without owning the spirit.

Revision ID: 20261104_0127
Revises: 20261104_0126
Create Date: 2026-11-04

The API role still cannot delete a skill. The worker updates points and rank only.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261104_0127"
down_revision: str | None = "20261104_0126"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "public.craft_skills"
_OWNER = (
    "EXISTS (SELECT 1 FROM public.spirits s "
    "WHERE s.id = spirit_id AND s.user_id = auth.uid())"
)


def upgrade() -> None:
    op.execute(f"REVOKE INSERT, UPDATE ON TABLE {_TABLE} FROM kelin_worker")
    for command in ("select", "insert", "update"):
        op.execute(
            f"DROP POLICY IF EXISTS rls_craft_skills_kelin_worker_{command} ON {_TABLE}"
        )
    op.execute(
        "CREATE POLICY rls_craft_skills_kelin_worker_select "
        f"ON {_TABLE} AS PERMISSIVE FOR SELECT TO kelin_worker USING (true)"
    )
    op.execute(
        "CREATE POLICY rls_craft_skills_kelin_worker_update "
        f"ON {_TABLE} AS PERMISSIVE FOR UPDATE TO kelin_worker "
        "USING (true) WITH CHECK (true)"
    )
    op.execute(f"GRANT UPDATE (points, rank_name) ON TABLE {_TABLE} TO kelin_worker")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS rls_craft_skills_kelin_worker_select ON " + _TABLE
    )
    op.execute(
        "DROP POLICY IF EXISTS rls_craft_skills_kelin_worker_update ON " + _TABLE
    )
    op.execute(f"REVOKE UPDATE (points, rank_name) ON TABLE {_TABLE} FROM kelin_worker")
    op.execute(f"GRANT INSERT, UPDATE ON TABLE {_TABLE} TO kelin_worker")
    op.execute(
        "CREATE POLICY rls_craft_skills_kelin_worker_select "
        f"ON {_TABLE} AS PERMISSIVE FOR SELECT TO kelin_worker USING ({_OWNER})"
    )
    op.execute(
        "CREATE POLICY rls_craft_skills_kelin_worker_insert "
        f"ON {_TABLE} AS PERMISSIVE FOR INSERT TO kelin_worker WITH CHECK ({_OWNER})"
    )
    op.execute(
        "CREATE POLICY rls_craft_skills_kelin_worker_update "
        f"ON {_TABLE} AS PERMISSIVE FOR UPDATE TO kelin_worker "
        f"USING ({_OWNER}) WITH CHECK ({_OWNER})"
    )
