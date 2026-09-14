"""AI usage rollup and budget alert functions. Spec §§16.6, 17.2.

Revision ID: 20260908_0011
Revises: 20260908_0010
Create Date: 2026-09-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0011"
down_revision: str | None = "20260908_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute(f"GRANT SELECT ON TABLE public.ai_usage TO {_DEFINER}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE public.outbox_events TO {_DEFINER}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.rollup_ai_usage(p_date date)
        RETURNS TABLE(user_count bigint, request_count bigint, total_micros bigint)
        LANGUAGE sql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
          SELECT
            COUNT(DISTINCT user_id)::bigint,
            COUNT(*)::bigint,
            COALESCE(SUM(estimated_cost_micros), 0)::bigint
          FROM public.ai_usage
          WHERE ((created_at AT TIME ZONE 'UTC')::date) = p_date
        $$
        """
    )
    op.execute(f"ALTER FUNCTION private.rollup_ai_usage(date) OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION private.rollup_ai_usage(date) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION private.rollup_ai_usage(date) TO kelin_worker")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.emit_due_budget_alerts(
          p_date date,
          p_budget_micros bigint,
          p_warning_percent integer,
          p_critical_percent integer
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          inserted integer := 0;
          extra integer := 0;
        BEGIN
          IF p_date IS NULL OR p_budget_micros IS NULL OR p_budget_micros <= 0 THEN
            RAISE EXCEPTION 'budget inputs are required';
          END IF;
          INSERT INTO public.outbox_events (
            aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload, available_at
          )
          SELECT
            'usage',
            s.user_id,
            'usage.budget.warning',
            'usage-budget-warning:' || s.user_id::text || ':' || p_date::text,
            s.user_id,
            jsonb_build_object(
              'threshold', 'warning',
              'usage_date', p_date,
              'estimated_cost_micros', s.spent,
              'budget_micros', p_budget_micros
            ),
            now()
          FROM (
            SELECT user_id, COALESCE(SUM(estimated_cost_micros), 0) AS spent
            FROM public.ai_usage
            WHERE ((created_at AT TIME ZONE 'UTC')::date) = p_date
            GROUP BY user_id
          ) s
          WHERE (s.spent * 100) >= (p_budget_micros * p_warning_percent::bigint)
          ON CONFLICT (dedupe_key) DO NOTHING;
          GET DIAGNOSTICS inserted = ROW_COUNT;
          INSERT INTO public.outbox_events (
            aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload, available_at
          )
          SELECT
            'usage',
            s.user_id,
            'usage.budget.critical',
            'usage-budget-critical:' || s.user_id::text || ':' || p_date::text,
            s.user_id,
            jsonb_build_object(
              'threshold', 'critical',
              'usage_date', p_date,
              'estimated_cost_micros', s.spent,
              'budget_micros', p_budget_micros
            ),
            now()
          FROM (
            SELECT user_id, COALESCE(SUM(estimated_cost_micros), 0) AS spent
            FROM public.ai_usage
            WHERE ((created_at AT TIME ZONE 'UTC')::date) = p_date
            GROUP BY user_id
          ) s
          WHERE (s.spent * 100) >= (p_budget_micros * p_critical_percent::bigint)
          ON CONFLICT (dedupe_key) DO NOTHING;
          GET DIAGNOSTICS extra = ROW_COUNT;
          RETURN inserted + extra;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.emit_due_budget_alerts(date, bigint, integer, integer) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.emit_due_budget_alerts(date, bigint, integer, integer) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.emit_due_budget_alerts(date, bigint, integer, integer) "
        "TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.emit_owner_budget_alert(
          p_date date,
          p_owner_id uuid,
          p_threshold text,
          p_spent_micros bigint,
          p_budget_micros bigint
        )
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          uid uuid := NULLIF(current_setting('request.jwt.claim.sub', true), '')::uuid;
          inserted uuid;
          event text;
          key text;
        BEGIN
          IF uid IS NULL OR uid IS DISTINCT FROM p_owner_id THEN
            RAISE EXCEPTION 'claim required';
          END IF;
          IF p_threshold NOT IN ('warning', 'critical') THEN
            RAISE EXCEPTION 'invalid threshold';
          END IF;
          event := 'usage.budget.' || p_threshold;
          key := 'usage-budget-' || p_threshold || ':' || uid::text || ':' || p_date::text;
          INSERT INTO public.outbox_events (
            aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload, available_at
          ) VALUES (
            'usage',
            uid,
            event,
            key,
            uid,
            jsonb_build_object(
              'threshold', p_threshold,
              'usage_date', p_date,
              'estimated_cost_micros', p_spent_micros,
              'budget_micros', p_budget_micros
            ),
            now()
          )
          ON CONFLICT (dedupe_key) DO NOTHING
          RETURNING id INTO inserted;
          RETURN inserted IS NOT NULL;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.emit_owner_budget_alert(date, uuid, text, bigint, bigint) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.emit_owner_budget_alert(date, uuid, text, bigint, bigint) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "private.emit_owner_budget_alert(date, uuid, text, bigint, bigint) "
        "TO kelin_api"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS private.emit_owner_budget_alert(date, uuid, text, bigint, bigint)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS private.emit_due_budget_alerts(date, bigint, integer, integer)"
    )
    op.execute("DROP FUNCTION IF EXISTS private.rollup_ai_usage(date)")
    op.execute(f"REVOKE SELECT ON TABLE public.ai_usage FROM {_DEFINER}")
