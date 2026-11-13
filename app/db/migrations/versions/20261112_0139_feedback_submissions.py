"""Feedback rows. Consents stay on the existing account_consents table.

Revision ID: 20261112_0139
Revises: 20261110_0138
Create Date: 2026-11-12

The table is owner-written by the API role. Deleting auth.users cascades.
"""

from collections.abc import Sequence

from alembic import op
from app.db.rls_matrix import (
    RLS_MATRIX_FEEDBACK,
    enable_force_statements,
    grant_statements,
    policy_create_statements,
)

revision: str = "20261112_0139"
down_revision: str | None = "20261110_0138"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RESIDUE_WITH_FEEDBACK = """
CREATE OR REPLACE FUNCTION private.account_residue_exists(p_owner_id uuid)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public, private, auth
AS $$
  SELECT COALESCE(
    EXISTS (SELECT 1 FROM public.spirits WHERE user_id = p_owner_id)
    OR EXISTS (SELECT 1 FROM public.user_preferences WHERE user_id = p_owner_id)
    OR EXISTS (SELECT 1 FROM public.devices WHERE user_id = p_owner_id)
    OR EXISTS (
      SELECT 1 FROM public.feedback_submissions WHERE user_id = p_owner_id
    )
    OR EXISTS (SELECT 1 FROM auth.users WHERE id = p_owner_id),
    false
  )
$$
"""

_RESIDUE_WITHOUT_FEEDBACK = """
CREATE OR REPLACE FUNCTION private.account_residue_exists(p_owner_id uuid)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public, private, auth
AS $$
  SELECT COALESCE(
    EXISTS (SELECT 1 FROM public.spirits WHERE user_id = p_owner_id)
    OR EXISTS (SELECT 1 FROM public.user_preferences WHERE user_id = p_owner_id)
    OR EXISTS (SELECT 1 FROM public.devices WHERE user_id = p_owner_id)
    OR EXISTS (SELECT 1 FROM auth.users WHERE id = p_owner_id),
    false
  )
$$
"""


def _own_residue() -> None:
    op.execute("ALTER FUNCTION private.account_residue_exists(uuid) OWNER TO kelin_definer")
    op.execute("REVOKE ALL ON FUNCTION private.account_residue_exists(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION private.account_residue_exists(uuid) TO kelin_worker")


def upgrade() -> None:
    op.execute(
        "CREATE TABLE public.feedback_submissions ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "user_id uuid NOT NULL "
        "REFERENCES auth.users (id) ON DELETE CASCADE, "
        "client_id uuid NOT NULL, "
        "body text NOT NULL, "
        "created_at timestamptz NOT NULL DEFAULT now(), "
        "CONSTRAINT uq_feedback_submissions_user_client "
        "UNIQUE (user_id, client_id), "
        "CONSTRAINT ck_feedback_submissions_body "
        "CHECK (char_length(body) BETWEEN 1 AND 1000))"
    )
    for statement in enable_force_statements(RLS_MATRIX_FEEDBACK):
        op.execute(statement)
    for statement in policy_create_statements(RLS_MATRIX_FEEDBACK):
        op.execute(statement)
    for statement in grant_statements(RLS_MATRIX_FEEDBACK):
        op.execute(statement)
    op.execute(_RESIDUE_WITH_FEEDBACK)
    _own_residue()


def downgrade() -> None:
    op.execute(_RESIDUE_WITHOUT_FEEDBACK)
    _own_residue()
    op.drop_table("feedback_submissions")
