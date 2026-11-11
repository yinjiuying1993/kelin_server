"""Directory card and the two public-profile fields.

Revision ID: 20261110_0132
Revises: 20261110_0131
Create Date: 2026-11-10

kelin_api cannot read another user's spirit row. These functions return only
the directory card or the public face.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261110_0132"
down_revision: str | None = "20261110_0131"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.v4_spirit_public_face(target uuid)
        RETURNS TABLE (display_name text, avatar_key text)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT
            COALESCE(NULLIF(btrim(nickname), ''), name),
            COALESCE(custom_avatar_key, default_avatar_key)
          FROM public.spirits
          WHERE id = target
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.v4_spirit_directory(target uuid, viewer uuid)
        RETURNS TABLE (
          spirit_id uuid,
          owner_user_id uuid,
          display_name text,
          avatar_key text,
          available boolean
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT
            s.id,
            s.user_id,
            COALESCE(NULLIF(btrim(s.nickname), ''), s.name),
            COALESCE(s.custom_avatar_key, s.default_avatar_key),
            NOT EXISTS (
              SELECT 1
              FROM public.account_deletions AS deletion
              WHERE deletion.owner_id = s.user_id
                AND deletion.status <> 'completed'
            )
          FROM public.spirits AS s
          WHERE s.id = target
            AND viewer IS NOT NULL
            AND s.user_id <> viewer
        $$
        """
    )
    for name, args in (
        ("v4_spirit_public_face", "uuid"),
        ("v4_spirit_directory", "uuid, uuid"),
    ):
        op.execute(f"ALTER FUNCTION public.{name}({args}) OWNER TO {_DEFINER}")
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}({args}) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{name}({args}) TO kelin_api")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.v4_spirit_directory(uuid, uuid)")
    op.execute("DROP FUNCTION IF EXISTS public.v4_spirit_public_face(uuid)")
