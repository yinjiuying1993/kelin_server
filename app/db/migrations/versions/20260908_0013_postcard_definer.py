"""Postcard list/read security definer functions. Spec §§7.4, 14.4–14.5.

Revision ID: 20260908_0013
Revises: 20260908_0012
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0013"
down_revision: str | None = "20260908_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"

_ROW_SELECT = """
          SELECT
            p.id,
            p.created_at,
            p.read_at,
            p.visit_id,
            p.text,
            CASE WHEN sender.id IS NULL THEN NULL ELSE jsonb_build_object(
              'id', sender.id,
              'title', sender.name,
              'stage', sender.stage,
              'public_marks', to_jsonb(COALESCE(sender.scholar_marks, ARRAY[]::text[])),
              'status', sender.status
            ) END,
            CASE WHEN postcard_npc.npc_id IS NULL THEN NULL ELSE jsonb_build_object(
              'npc_id', postcard_npc.npc_id,
              'title', postcard_npc.title,
              'public_marks', to_jsonb(COALESCE(postcard_npc.public_marks, ARRAY[]::text[]))
            ) END,
            jsonb_build_object(
              'type', 'visit',
              'id', v.id,
              'plan_id', v.plan_id,
              'destination_index', v.destination_index,
              'status', v.status,
              'host', CASE WHEN host.id IS NULL THEN NULL ELSE jsonb_build_object(
                'id', host.id,
                'title', host.name,
                'stage', host.stage,
                'public_marks', to_jsonb(COALESCE(host.scholar_marks, ARRAY[]::text[])),
                'status', host.status
              ) END,
              'npc', CASE WHEN visit_npc.npc_id IS NULL THEN NULL ELSE jsonb_build_object(
                'npc_id', visit_npc.npc_id,
                'title', visit_npc.title,
                'public_marks', to_jsonb(COALESCE(visit_npc.public_marks, ARRAY[]::text[]))
              ) END,
              'public_context', jsonb_build_object(
                'title', COALESCE(
                  NULLIF(v.public_context->>'title', ''),
                  host.name,
                  visit_npc.title,
                  '未名'
                ),
                'stage', COALESCE(
                  NULLIF(v.public_context->>'stage', ''),
                  host.stage,
                  'formed'
                ),
                'weather', COALESCE(NULLIF(v.public_context->>'weather', ''), 'cloudy'),
                'public_marks', COALESCE(
                  v.public_context->'public_marks',
                  to_jsonb(COALESCE(host.scholar_marks, visit_npc.public_marks, ARRAY[]::text[]))
                )
              )
            )
          FROM public.postcards p
          JOIN public.visits v ON v.id = p.visit_id
          LEFT JOIN public.spirits sender ON sender.id = p.sender_spirit_id
          LEFT JOIN public.spirits host ON host.id = v.host_spirit_id
          LEFT JOIN public.npc_profiles postcard_npc
            ON postcard_npc.npc_id = p.npc_id
           AND postcard_npc.config_version = COALESCE(v.npc_config_version, 1)
          LEFT JOIN public.npc_profiles visit_npc
            ON visit_npc.npc_id = v.npc_id
           AND visit_npc.config_version = COALESCE(v.npc_config_version, 1)
"""


def upgrade() -> None:
    op.execute(f"GRANT SELECT ON TABLE public.postcards TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.visits TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.npc_profiles TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.spirits TO {_DEFINER}")
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION private.list_postcards_page(
          p_owner_spirit_id uuid,
          p_unread_only boolean,
          p_snapshot_at timestamptz,
          p_fetch_limit integer,
          p_cursor_time timestamptz,
          p_cursor_id uuid
        )
        RETURNS TABLE (
          postcard_id uuid,
          created_at timestamptz,
          read_at timestamptz,
          visit_id uuid,
          body text,
          sender jsonb,
          npc jsonb,
          visit jsonb
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          uid uuid := auth.uid();
          caller uuid;
          lim integer;
        BEGIN
          IF uid IS NULL OR p_owner_spirit_id IS NULL OR p_snapshot_at IS NULL THEN
            RETURN;
          END IF;
          SELECT s.id INTO caller
          FROM public.spirits s
          WHERE s.user_id = uid;
          IF caller IS NULL OR caller IS DISTINCT FROM p_owner_spirit_id THEN
            RETURN;
          END IF;
          lim := LEAST(GREATEST(COALESCE(p_fetch_limit, 0), 0), 51);
          IF lim < 1 THEN
            RETURN;
          END IF;
          RETURN QUERY
          {_ROW_SELECT}
          WHERE p.receiver_spirit_id = caller
            AND (
              (p.sender_spirit_id IS NOT NULL AND p.npc_id IS NULL)
              OR (p.sender_spirit_id IS NULL AND p.npc_id IS NOT NULL)
            )
            AND (p.npc_id IS NULL OR postcard_npc.npc_id IS NOT NULL)
            AND p.created_at <= p_snapshot_at
            AND (NOT COALESCE(p_unread_only, false) OR p.read_at IS NULL)
            AND (
              p_cursor_time IS NULL
              OR p.created_at < p_cursor_time
              OR (p.created_at = p_cursor_time AND p.id < p_cursor_id)
            )
          ORDER BY p.created_at DESC, p.id DESC
          LIMIT lim;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.list_postcards_page("
        "uuid, boolean, timestamptz, integer, timestamptz, uuid) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.list_postcards_page("
        "uuid, boolean, timestamptz, integer, timestamptz, uuid) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.list_postcards_page("
        "uuid, boolean, timestamptz, integer, timestamptz, uuid) TO kelin_api"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION private.postcard_public(p_postcard_id uuid)
        RETURNS TABLE (
          postcard_id uuid,
          created_at timestamptz,
          read_at timestamptz,
          visit_id uuid,
          body text,
          sender jsonb,
          npc jsonb,
          visit jsonb
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          uid uuid := auth.uid();
          caller uuid;
        BEGIN
          IF uid IS NULL OR p_postcard_id IS NULL THEN
            RETURN;
          END IF;
          SELECT s.id INTO caller
          FROM public.spirits s
          WHERE s.user_id = uid;
          IF caller IS NULL THEN
            RETURN;
          END IF;
          RETURN QUERY
          {_ROW_SELECT}
          WHERE p.id = p_postcard_id
            AND p.receiver_spirit_id = caller
            AND (
              (p.sender_spirit_id IS NOT NULL AND p.npc_id IS NULL)
              OR (p.sender_spirit_id IS NULL AND p.npc_id IS NOT NULL)
            );
        END;
        $$
        """
    )
    op.execute(f"ALTER FUNCTION private.postcard_public(uuid) OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION private.postcard_public(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION private.postcard_public(uuid) TO kelin_api")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.postcard_public(uuid)")
    op.execute(
        "DROP FUNCTION IF EXISTS private.list_postcards_page("
        "uuid, boolean, timestamptz, integer, timestamptz, uuid)"
    )
    op.execute(f"REVOKE ALL ON TABLE public.postcards FROM {_DEFINER}")
    op.execute(f"REVOKE ALL ON TABLE public.visits FROM {_DEFINER}")
    op.execute(f"REVOKE ALL ON TABLE public.npc_profiles FROM {_DEFINER}")
