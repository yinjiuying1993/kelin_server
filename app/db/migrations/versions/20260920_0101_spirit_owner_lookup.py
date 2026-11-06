"""Worker lookup of spirit owner for visit-target settle. S3-T05.

Revision ID: 20260920_0101
Revises: 20260918_0100
Create Date: 2026-09-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260920_0101"
down_revision: str | None = "20260918_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute(f"GRANT SELECT ON TABLE public.spirits TO {_DEFINER}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.spirit_owner_id(p_spirit_id uuid)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
          SELECT s.user_id
          FROM public.spirits s
          WHERE s.id = p_spirit_id
        $$
        """
    )
    op.execute(
        f"ALTER FUNCTION private.spirit_owner_id(uuid) OWNER TO {_DEFINER}"
    )
    op.execute("REVOKE ALL ON FUNCTION private.spirit_owner_id(uuid) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.spirit_owner_id(uuid) TO kelin_worker"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.spirit_owner_id(uuid)")
