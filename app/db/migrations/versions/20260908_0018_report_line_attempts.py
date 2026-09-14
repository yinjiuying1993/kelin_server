"""Cap report line attempts and enqueue report.generate via definer. Spec §§14.7–14.8.

Revision ID: 20260908_0018
Revises: 20260908_0017
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0018"
down_revision: str | None = "20260908_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.reports
          DROP CONSTRAINT IF EXISTS ck_reports_line_attempts
        """
    )
    op.execute(
        """
        ALTER TABLE public.reports
          ADD CONSTRAINT ck_reports_line_attempts
          CHECK (line_attempts >= 0 AND line_attempts <= 3)
        """
    )
    op.execute(f"GRANT SELECT, INSERT ON TABLE public.outbox_events TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.reports TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.spirits TO {_DEFINER}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.enqueue_report_generate(
          p_report_id uuid,
          p_spirit_id uuid
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          inserted_id uuid;
        BEGIN
          IF p_report_id IS NULL OR p_spirit_id IS NULL THEN
            RAISE EXCEPTION 'report enqueue requires ids';
          END IF;
          IF auth.uid() IS NULL THEN
            RAISE EXCEPTION 'owner claim required';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM public.reports r
            JOIN public.spirits s ON s.id = r.spirit_id AND s.user_id = auth.uid()
            WHERE r.id = p_report_id AND r.spirit_id = p_spirit_id
          ) THEN
            RETURN false;
          END IF;
          INSERT INTO public.outbox_events (
            aggregate_type,
            aggregate_id,
            event_type,
            dedupe_key,
            owner_id,
            payload
          ) VALUES (
            'report',
            p_spirit_id,
            'report.generate',
            'report-generate:' || p_report_id::text,
            auth.uid(),
            jsonb_build_object('resource_id', p_report_id)
          )
          ON CONFLICT (dedupe_key) DO NOTHING
          RETURNING id INTO inserted_id;
          RETURN inserted_id IS NOT NULL;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.enqueue_report_generate(uuid, uuid) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute("REVOKE ALL ON FUNCTION private.enqueue_report_generate(uuid, uuid) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.enqueue_report_generate(uuid, uuid) TO kelin_api"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.enqueue_report_generate(uuid, uuid)")
    op.execute(f"REVOKE SELECT ON TABLE public.reports FROM {_DEFINER}")
    op.execute(
        """
        ALTER TABLE public.reports
          DROP CONSTRAINT IF EXISTS ck_reports_line_attempts
        """
    )
    op.execute(
        """
        ALTER TABLE public.reports
          ADD CONSTRAINT ck_reports_line_attempts
          CHECK (line_attempts >= 0)
        """
    )
