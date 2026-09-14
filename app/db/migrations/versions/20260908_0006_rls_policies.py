"""Enable FORCE RLS, auth.uid(), policies, and minimum grants.

Revision ID: 20260908_0006
Revises: 20260908_0005
Create Date: 2026-09-08
"""

from collections.abc import Sequence

from alembic import op
from app.db.rls_matrix import (
    RLS_MATRIX,
    enable_force_statements,
    grant_statements,
    policy_blueprints,
    policy_create_statements,
)
from app.db.roles import RUNTIME_ROLES

revision: str = "20260908_0006"
down_revision: str | None = "20260908_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNTIME = ", ".join(RUNTIME_ROLES)


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA auth TO kelin_api, kelin_worker, kelin_scheduler")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth.uid()
        RETURNS uuid
        LANGUAGE sql
        STABLE
        PARALLEL SAFE
        SET search_path = pg_catalog, auth
        AS $$
          SELECT NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth.uid() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth.uid() TO kelin_api, kelin_worker, kelin_scheduler")

    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {_RUNTIME}")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM kelin_api, kelin_worker")

    for statement in enable_force_statements():
        op.execute(statement)

    for row in RLS_MATRIX:
        op.execute(f"ALTER TABLE public.{row.table} OWNER TO kelin_migrator")

    op.execute(
        """
        DO $seq$
        DECLARE
          seq_name text;
        BEGIN
          FOR seq_name IN
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'S'
          LOOP
            EXECUTE format('ALTER SEQUENCE public.%I OWNER TO kelin_migrator', seq_name);
            EXECUTE format(
              'GRANT USAGE, SELECT ON SEQUENCE public.%I TO kelin_api, kelin_worker',
              seq_name
            );
          END LOOP;
        END
        $seq$;
        """
    )

    for statement in policy_create_statements():
        op.execute(statement)
    for statement in grant_statements():
        op.execute(statement)


def downgrade() -> None:
    for blueprint in reversed(policy_blueprints()):
        op.execute(f"DROP POLICY IF EXISTS {blueprint.name} ON public.{blueprint.table}")

    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {_RUNTIME}")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM kelin_api, kelin_worker")

    for row in RLS_MATRIX:
        op.execute(f"ALTER TABLE public.{row.table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{row.table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{row.table} OWNER TO CURRENT_USER")

    op.execute(
        """
        DO $seq$
        DECLARE
          seq_name text;
        BEGIN
          FOR seq_name IN
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'S'
          LOOP
            EXECUTE format('ALTER SEQUENCE public.%I OWNER TO CURRENT_USER', seq_name);
          END LOOP;
        END
        $seq$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS auth.uid()")
    op.execute("REVOKE USAGE ON SCHEMA auth FROM kelin_api, kelin_worker, kelin_scheduler")
