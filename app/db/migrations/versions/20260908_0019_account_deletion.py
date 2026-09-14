"""Account deletion definer functions. Spec §§6.5, 7.2, 14.9.

Revision ID: 20260908_0019
Revises: 20260908_0018
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0019"
down_revision: str | None = "20260908_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_account_deletions_owner_active
        ON public.account_deletions (owner_id)
        WHERE status IS DISTINCT FROM 'completed'
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.account_deletions TO {_DEFINER}")
    op.execute(f"GRANT SELECT, UPDATE ON TABLE public.devices TO {_DEFINER}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE public.outbox_events TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.spirits TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.user_preferences TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE auth.users TO {_DEFINER}")
    op.execute(f"GRANT DELETE ON TABLE auth.users TO {_DEFINER}")
    op.execute(f"GRANT DELETE ON ALL TABLES IN SCHEMA public TO {_DEFINER}")
    op.execute(f"GRANT DELETE ON TABLE private.quota_reservations TO {_DEFINER}")
    op.execute(f"GRANT USAGE ON SCHEMA private TO {_DEFINER}, kelin_migrator")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.account_is_deleting()
        RETURNS boolean
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
          SELECT EXISTS (
            SELECT 1
            FROM public.account_deletions
            WHERE owner_id = auth.uid()
          )
        $$
        """
    )
    op.execute(f"ALTER FUNCTION private.account_is_deleting() OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION private.account_is_deleting() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION private.account_is_deleting() TO kelin_api")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.mark_account_deleting(
          p_client_id uuid,
          p_owner_hash text
        )
        RETURNS TABLE (
          deletion_id uuid,
          requested_at timestamptz,
          outcome text
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          inserted_id uuid;
          existing_id uuid;
          existing_at timestamptz;
        BEGIN
          IF p_client_id IS NULL THEN
            RAISE EXCEPTION 'client_id required';
          END IF;
          IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'owner claim required';
          END IF;
          BEGIN
            INSERT INTO public.account_deletions (
              owner_id, client_id, owner_hash, status
            ) VALUES (
              auth.uid(), p_client_id, p_owner_hash, 'accepted'
            )
            ON CONFLICT (owner_id, client_id) DO NOTHING
            RETURNING id INTO inserted_id;
          EXCEPTION WHEN unique_violation THEN
            RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, 'pending'::text;
            RETURN;
          END;
          SELECT d.id, d.requested_at
            INTO existing_id, existing_at
          FROM public.account_deletions d
          WHERE d.owner_id = auth.uid() AND d.client_id = p_client_id;
          IF existing_id IS NULL THEN
            RETURN QUERY SELECT NULL::uuid, NULL::timestamptz, 'pending'::text;
            RETURN;
          END IF;
          IF inserted_id IS NOT NULL THEN
            UPDATE public.devices
            SET notifications_enabled = false, updated_at = now()
            WHERE user_id = auth.uid() AND notifications_enabled = true;
            INSERT INTO public.outbox_events (
              aggregate_type,
              aggregate_id,
              event_type,
              dedupe_key,
              owner_id,
              payload
            ) VALUES (
              'account',
              existing_id,
              'account.delete',
              'account-delete:' || existing_id::text,
              auth.uid(),
              jsonb_build_object('deletion_id', existing_id)
            )
            ON CONFLICT (dedupe_key) DO NOTHING;
            RETURN QUERY SELECT existing_id, existing_at, 'inserted'::text;
            RETURN;
          END IF;
          RETURN QUERY SELECT existing_id, existing_at, 'replay'::text;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.mark_account_deleting(uuid, text) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute("REVOKE ALL ON FUNCTION private.mark_account_deleting(uuid, text) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.mark_account_deleting(uuid, text) TO kelin_api"
    )
    op.execute(
        """
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
    )
    op.execute(
        f"ALTER FUNCTION private.account_residue_exists(uuid) OWNER TO {_DEFINER}"
    )
    op.execute("REVOKE ALL ON FUNCTION private.account_residue_exists(uuid) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.account_residue_exists(uuid) TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.delete_auth_user(p_user_id uuid)
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private, auth
        AS $$
        BEGIN
          IF p_user_id IS NULL THEN
            RAISE EXCEPTION 'user_id required';
          END IF;
          DELETE FROM auth.users WHERE id = p_user_id;
        END;
        $$
        """
    )
    op.execute(f"ALTER FUNCTION public.delete_auth_user(uuid) OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION public.delete_auth_user(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.delete_auth_user(uuid) TO kelin_worker")
    op.execute(
        """
        DO $grant$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kelin_test') THEN
            GRANT EXECUTE ON FUNCTION public.delete_auth_user(uuid) TO kelin_test;
          END IF;
        END
        $grant$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.delete_auth_user(uuid)")
    op.execute("DROP FUNCTION IF EXISTS private.account_residue_exists(uuid)")
    op.execute("DROP FUNCTION IF EXISTS private.mark_account_deleting(uuid, text)")
    op.execute("DROP FUNCTION IF EXISTS private.account_is_deleting()")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON TABLE public.account_deletions FROM {_DEFINER}")
    op.execute(f"REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM {_DEFINER}")
    op.execute(f"REVOKE DELETE ON TABLE private.quota_reservations FROM {_DEFINER}")
    op.execute("REVOKE USAGE ON SCHEMA private FROM kelin_migrator")
    op.execute(f"REVOKE SELECT, DELETE ON TABLE auth.users FROM {_DEFINER}")
    op.execute("DROP INDEX IF EXISTS public.uq_account_deletions_owner_active")
