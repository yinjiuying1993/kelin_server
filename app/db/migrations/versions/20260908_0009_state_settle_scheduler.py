"""Scheduler due-state enqueue. Spec §§8.6, 17.2.

Revision ID: 20260908_0009
Revises: 20260908_0008
Create Date: 2026-09-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0009"
down_revision: str | None = "20260908_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute(
        f"""
        DO $role$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_DEFINER}') THEN
            CREATE ROLE {_DEFINER}
              NOLOGIN
              NOSUPERUSER
              NOCREATEDB
              NOCREATEROLE
              NOREPLICATION
              INHERIT
              BYPASSRLS;
          ELSE
            ALTER ROLE {_DEFINER} WITH
              NOLOGIN
              NOSUPERUSER
              NOCREATEDB
              NOCREATEROLE
              NOREPLICATION
              INHERIT
              BYPASSRLS;
          END IF;
        END
        $role$;
        """
    )
    op.execute("CREATE SCHEMA IF NOT EXISTS private")
    op.execute("REVOKE ALL ON SCHEMA private FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_DEFINER}")
    op.execute(f"GRANT USAGE ON SCHEMA private TO kelin_scheduler, {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.spirits TO {_DEFINER}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE public.outbox_events TO {_DEFINER}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.enqueue_due_state_jobs(p_now timestamptz)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          inserted integer := 0;
          bucket text := to_char((p_now AT TIME ZONE 'UTC'), 'YYYYMMDDHH24');
        BEGIN
          IF p_now IS NULL THEN
            RAISE EXCEPTION 'now is required';
          END IF;
          INSERT INTO public.outbox_events (
            aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload, available_at
          )
          SELECT
            'spirit',
            s.id,
            'state.settle',
            'state:' || s.id::text || ':' || bucket,
            s.user_id,
            jsonb_build_object('resource_id', s.id),
            p_now
          FROM public.spirits s
          WHERE s.last_interact_at <= p_now - interval '18 hours'
             OR (s.study_until IS NOT NULL AND s.study_until <= p_now)
             OR (s.away_until IS NOT NULL AND s.away_until <= p_now)
          ON CONFLICT (dedupe_key) DO NOTHING;
          GET DIAGNOSTICS inserted = ROW_COUNT;
          RETURN inserted;
        END;
        $$
        """
    )
    op.execute(f"ALTER FUNCTION private.enqueue_due_state_jobs(timestamptz) OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION private.enqueue_due_state_jobs(timestamptz) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.enqueue_due_state_jobs(timestamptz) TO kelin_scheduler"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.enqueue_due_state_jobs(timestamptz)")
    op.execute(f"REVOKE ALL ON TABLE public.spirits FROM {_DEFINER}")
    op.execute(f"REVOKE ALL ON TABLE public.outbox_events FROM {_DEFINER}")
    op.execute(f"REVOKE ALL ON SCHEMA public FROM {_DEFINER}")
    op.execute("DROP SCHEMA IF EXISTS private")
    op.execute(f"DROP ROLE IF EXISTS {_DEFINER}")
