"""Friend undirected-edge security definer functions. Spec §§7.4, 8.9, 14.1–14.3.

Revision ID: 20260908_0012
Revises: 20260908_0011
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0012"
down_revision: str | None = "20260908_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA auth TO kelin_definer")
    op.execute("GRANT EXECUTE ON FUNCTION auth.uid() TO kelin_definer")
    op.execute(f"GRANT SELECT ON TABLE public.spirits TO {_DEFINER}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON TABLE public.friends TO {_DEFINER}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.add_friend_by_code(p_invite_code text)
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          uid uuid := auth.uid();
          caller uuid;
          peer uuid;
          low_id uuid;
          high_id uuid;
          new_id uuid;
          code text;
        BEGIN
          IF uid IS NULL THEN
            RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
          END IF;
          code := upper(btrim(COALESCE(p_invite_code, '')));
          SELECT s.id INTO caller
          FROM public.spirits s
          WHERE s.user_id = uid;
          IF caller IS NULL THEN
            RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
          END IF;
          SELECT s.id INTO peer
          FROM public.spirits s
          WHERE s.invite_code = code;
          IF peer IS NULL THEN
            RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
          END IF;
          IF peer = caller THEN
            RAISE EXCEPTION 'SELF_FRIEND_NOT_ALLOWED' USING ERRCODE = 'P0001';
          END IF;
          IF caller < peer THEN
            low_id := caller;
            high_id := peer;
          ELSE
            low_id := peer;
            high_id := caller;
          END IF;
          PERFORM pg_advisory_xact_lock(hashtext(low_id::text), hashtext(high_id::text));
          INSERT INTO public.friends (
            spirit_low_id, spirit_high_id, created_by_spirit_id
          ) VALUES (
            low_id, high_id, caller
          )
          ON CONFLICT ON CONSTRAINT uq_friends_spirit_low_id_spirit_high_id
          DO NOTHING
          RETURNING id INTO new_id;
          IF new_id IS NOT NULL THEN
            RETURN new_id;
          END IF;
          SELECT f.id INTO new_id
          FROM public.friends f
          WHERE f.spirit_low_id = low_id AND f.spirit_high_id = high_id;
          RETURN new_id;
        END;
        $$
        """
    )
    op.execute(f"ALTER FUNCTION private.add_friend_by_code(text) OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION private.add_friend_by_code(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION private.add_friend_by_code(text) TO kelin_api")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.remove_friend_edge(p_friend_id uuid)
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          uid uuid := auth.uid();
          caller uuid;
          removed uuid;
        BEGIN
          IF uid IS NULL OR p_friend_id IS NULL THEN
            RETURN NULL;
          END IF;
          SELECT s.id INTO caller
          FROM public.spirits s
          WHERE s.user_id = uid;
          IF caller IS NULL THEN
            RETURN NULL;
          END IF;
          DELETE FROM public.friends f
          WHERE f.id = p_friend_id
            AND (f.spirit_low_id = caller OR f.spirit_high_id = caller)
          RETURNING f.id INTO removed;
          RETURN removed;
        END;
        $$
        """
    )
    op.execute(f"ALTER FUNCTION private.remove_friend_edge(uuid) OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION private.remove_friend_edge(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION private.remove_friend_edge(uuid) TO kelin_api")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.friend_edge_public(p_friend_id uuid)
        RETURNS TABLE (
          friend_id uuid,
          created_at timestamptz,
          peer_id uuid,
          title text,
          stage text,
          public_marks text[],
          status text
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          uid uuid := auth.uid();
          caller uuid;
        BEGIN
          IF uid IS NULL OR p_friend_id IS NULL THEN
            RETURN;
          END IF;
          SELECT s.id INTO caller
          FROM public.spirits s
          WHERE s.user_id = uid;
          IF caller IS NULL THEN
            RETURN;
          END IF;
          RETURN QUERY
          SELECT
            f.id,
            f.created_at,
            peer.id,
            peer.name,
            peer.stage,
            peer.scholar_marks,
            peer.status
          FROM public.friends f
          JOIN public.spirits peer ON peer.id = CASE
            WHEN f.spirit_low_id = caller THEN f.spirit_high_id
            ELSE f.spirit_low_id
          END
          WHERE f.id = p_friend_id
            AND (f.spirit_low_id = caller OR f.spirit_high_id = caller);
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.friend_edge_public(uuid) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute("REVOKE ALL ON FUNCTION private.friend_edge_public(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION private.friend_edge_public(uuid) TO kelin_api")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.list_friends_page(
          p_owner_spirit_id uuid,
          p_snapshot_at timestamptz,
          p_fetch_limit integer,
          p_cursor_time timestamptz,
          p_cursor_id uuid
        )
        RETURNS TABLE (
          friend_id uuid,
          created_at timestamptz,
          peer_id uuid,
          title text,
          stage text,
          public_marks text[],
          status text
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
          SELECT
            f.id,
            f.created_at,
            peer.id,
            peer.name,
            peer.stage,
            peer.scholar_marks,
            peer.status
          FROM public.friends f
          JOIN public.spirits peer ON peer.id = CASE
            WHEN f.spirit_low_id = caller THEN f.spirit_high_id
            ELSE f.spirit_low_id
          END
          WHERE (f.spirit_low_id = caller OR f.spirit_high_id = caller)
            AND f.created_at <= p_snapshot_at
            AND (
              p_cursor_time IS NULL
              OR f.created_at < p_cursor_time
              OR (f.created_at = p_cursor_time AND f.id < p_cursor_id)
            )
          ORDER BY f.created_at DESC, f.id DESC
          LIMIT lim;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.list_friends_page("
        "uuid, timestamptz, integer, timestamptz, uuid) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.list_friends_page("
        "uuid, timestamptz, integer, timestamptz, uuid) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.list_friends_page("
        "uuid, timestamptz, integer, timestamptz, uuid) TO kelin_api"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS private.list_friends_page("
        "uuid, timestamptz, integer, timestamptz, uuid)"
    )
    op.execute("DROP FUNCTION IF EXISTS private.friend_edge_public(uuid)")
    op.execute("DROP FUNCTION IF EXISTS private.remove_friend_edge(uuid)")
    op.execute("DROP FUNCTION IF EXISTS private.add_friend_by_code(text)")
    op.execute(f"REVOKE ALL ON TABLE public.friends FROM {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION auth.uid() FROM kelin_definer")
    op.execute("REVOKE ALL ON SCHEMA auth FROM kelin_definer")
