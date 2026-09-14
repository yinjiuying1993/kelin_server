"""Prepare NOBYPASSRLS runtime roles. 6.6 indexes already exist from 0003/0004.

Revision ID: 20260908_0005
Revises: 20260908_0004
Create Date: 2026-09-08
"""

from collections.abc import Sequence

from alembic import op
from app.db.roles import MIGRATOR_ROLE, PREPARED_ROLES, RUNTIME_ROLES

revision: str = "20260908_0005"
down_revision: str | None = "20260908_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ensure_role(name: str, *, login: bool) -> None:
    login_opt = "LOGIN" if login else "NOLOGIN"
    op.execute(
        f"""
        DO $role$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN
            CREATE ROLE {name}
              {login_opt}
              NOSUPERUSER
              NOCREATEDB
              NOCREATEROLE
              NOREPLICATION
              INHERIT
              NOBYPASSRLS;
          ELSE
            ALTER ROLE {name} WITH
              {login_opt}
              NOSUPERUSER
              NOCREATEDB
              NOCREATEROLE
              NOREPLICATION
              INHERIT
              NOBYPASSRLS;
          END IF;
        END
        $role$;
        """
    )
    op.execute(f"COMMENT ON ROLE {name} IS 'Kelin prepared database role (P02-T06)'")


def upgrade() -> None:
    for name in RUNTIME_ROLES:
        _ensure_role(name, login=True)
    _ensure_role(MIGRATOR_ROLE, login=False)

    op.execute(
        """
        GRANT USAGE ON SCHEMA public TO kelin_api, kelin_worker, kelin_scheduler,
          kelin_observer, kelin_migrator
        """
    )
    op.execute(
        """
        REVOKE CREATE ON SCHEMA public FROM kelin_api, kelin_worker, kelin_scheduler,
          kelin_observer
        """
    )
    op.execute("GRANT CREATE ON SCHEMA public TO kelin_migrator")
    op.execute(
        """
        DO $anon$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
            REVOKE ALL ON SCHEMA public FROM anon;
            REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
            REVOKE ALL ON SCHEMA public FROM authenticated;
            REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;
          END IF;
        END
        $anon$;
        """
    )


def downgrade() -> None:
    names = ", ".join(PREPARED_ROLES)
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {names}")
    for name in PREPARED_ROLES:
        op.execute(f"DROP ROLE IF EXISTS {name}")
