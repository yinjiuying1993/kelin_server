"""Read the columns a question and a public profile are allowed to see.

Revision ID: 20261104_0128
Revises: 20261104_0127
Create Date: 2026-11-04

The API role can select a public skill row. Spirit names, owner ids, and help
counts come from kelin_definer functions so the rest of those tables stays hidden.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261104_0128"
down_revision: str | None = "20261104_0127"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute("GRANT SELECT ON TABLE public.craft_ledgers TO kelin_definer")
    op.execute(
        "CREATE POLICY rls_craft_skills_kelin_api_public_select "
        "ON public.craft_skills AS PERMISSIVE FOR SELECT TO kelin_api "
        "USING (deleted_at IS NULL AND visibility = 'public')"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v3_spirit_public_name(target uuid)
        RETURNS text
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT name FROM public.spirits WHERE id = target
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v3_spirit_owner_id(target uuid)
        RETURNS uuid
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT user_id FROM public.spirits WHERE id = target
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.v3_ledger_help_count(target uuid)
        RETURNS integer
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT COALESCE(
            (SELECT help_count FROM public.craft_ledgers WHERE spirit_id = target),
            0
          )
        $$
        """
    )
    for name, args in (
        ("v3_spirit_public_name", "uuid"),
        ("v3_spirit_owner_id", "uuid"),
        ("v3_ledger_help_count", "uuid"),
    ):
        op.execute(f"ALTER FUNCTION public.{name}({args}) OWNER TO {_DEFINER}")
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}({args}) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{name}({args}) TO kelin_api")


def downgrade() -> None:
    for name, args in (
        ("v3_spirit_public_name", "uuid"),
        ("v3_spirit_owner_id", "uuid"),
        ("v3_ledger_help_count", "uuid"),
    ):
        op.execute(f"DROP FUNCTION IF EXISTS public.{name}({args})")
    op.execute(
        "DROP POLICY IF EXISTS rls_craft_skills_kelin_api_public_select "
        "ON public.craft_skills"
    )
