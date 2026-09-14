"""Visit planner: 12h eligibility and at most two destinations. Spec §§7.4, 8.9, 17.2.

Revision ID: 20260908_0014
Revises: 20260908_0013
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0014"
down_revision: str | None = "20260908_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute(f"GRANT SELECT ON TABLE public.spirits TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.friends TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.user_preferences TO {_DEFINER}")
    op.execute(f"GRANT SELECT ON TABLE public.npc_profiles TO {_DEFINER}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE public.visits TO {_DEFINER}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE public.outbox_events TO {_DEFINER}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.enqueue_due_visit_plan_jobs(p_now timestamptz)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          inserted integer := 0;
          bucket text;
        BEGIN
          IF p_now IS NULL THEN
            RAISE EXCEPTION 'now is required';
          END IF;
          bucket := to_char((p_now AT TIME ZONE 'UTC'), 'YYYYMMDD')
            || CASE
              WHEN EXTRACT(HOUR FROM (p_now AT TIME ZONE 'UTC')) < 12 THEN '00'
              ELSE '12'
            END;
          INSERT INTO public.outbox_events (
            aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload, available_at
          )
          SELECT
            'spirit',
            s.id,
            'visit.plan',
            'visit-plan:' || s.id::text || ':' || bucket,
            s.user_id,
            jsonb_build_object('schedule_bucket', bucket, 'resource_id', s.id),
            p_now
          FROM public.spirits s
          LEFT JOIN public.user_preferences vp ON vp.user_id = s.user_id
          WHERE s.status NOT IN ('lost', 'study')
            AND COALESCE(vp.visit_on, true) IS TRUE
            AND (
              s.status = 'away'
              OR s.last_interact_at <= p_now - interval '18 hours'
            )
          ON CONFLICT (dedupe_key) DO NOTHING;
          GET DIAGNOSTICS inserted = ROW_COUNT;
          RETURN inserted;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.enqueue_due_visit_plan_jobs(timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.enqueue_due_visit_plan_jobs(timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.enqueue_due_visit_plan_jobs(timestamptz) TO kelin_scheduler"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.plan_visits(p_now timestamptz)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          inserted integer := 0;
          inserted_one integer := 0;
          bucket text;
          visitor record;
          host_dests jsonb;
          npc_dests jsonb;
          dests jsonb;
          dest_item jsonb;
          idx integer;
          plan uuid;
        BEGIN
          IF p_now IS NULL THEN
            RAISE EXCEPTION 'now is required';
          END IF;
          bucket := to_char((p_now AT TIME ZONE 'UTC'), 'YYYYMMDD')
            || CASE
              WHEN EXTRACT(HOUR FROM (p_now AT TIME ZONE 'UTC')) < 12 THEN '00'
              ELSE '12'
            END;

          FOR visitor IN
            SELECT s.id AS spirit_id
            FROM public.spirits s
            LEFT JOIN public.user_preferences vp ON vp.user_id = s.user_id
            WHERE s.status NOT IN ('lost', 'study')
              AND COALESCE(vp.visit_on, true) IS TRUE
              AND (
                s.status = 'away'
                OR s.last_interact_at <= p_now - interval '18 hours'
              )
          LOOP
            PERFORM pg_advisory_xact_lock(hashtext('visit-plan:' || visitor.spirit_id::text));
            IF EXISTS (
              SELECT 1
              FROM public.visits v
              WHERE v.eligibility_key IN (
                'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || '1',
                'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || '2'
              )
            ) THEN
              CONTINUE;
            END IF;

            SELECT COALESCE(jsonb_agg(ranked.dest_row ORDER BY ranked.ord), '[]'::jsonb)
            INTO host_dests
            FROM (
              SELECT
                jsonb_build_object(
                  'kind', 'host',
                  'host_spirit_id', peer.id,
                  'public_context', jsonb_build_object(
                    'title', peer.name,
                    'stage', peer.stage,
                    'weather', 'cloudy',
                    'public_marks', to_jsonb(COALESCE(peer.scholar_marks, ARRAY[]::text[]))
                  )
                ) AS dest_row,
                row_number() OVER (ORDER BY f.created_at ASC, peer.id ASC) AS ord
              FROM public.friends f
              JOIN public.spirits peer ON peer.id = CASE
                WHEN f.spirit_low_id = visitor.spirit_id THEN f.spirit_high_id
                ELSE f.spirit_low_id
              END
              LEFT JOIN public.user_preferences hp ON hp.user_id = peer.user_id
              WHERE (f.spirit_low_id = visitor.spirit_id OR f.spirit_high_id = visitor.spirit_id)
                AND peer.id <> visitor.spirit_id
                AND peer.status <> 'lost'
                AND COALESCE(hp.visit_on, true) IS TRUE
              ORDER BY f.created_at ASC, peer.id ASC
              LIMIT 2
            ) ranked;
            host_dests := COALESCE(host_dests, '[]'::jsonb);

            SELECT COALESCE(jsonb_agg(picked.dest_row ORDER BY picked.npc_id), '[]'::jsonb)
            INTO npc_dests
            FROM (
              SELECT
                jsonb_build_object(
                  'kind', 'npc',
                  'npc_id', latest.npc_id,
                  'npc_config_version', latest.config_version,
                  'public_context', jsonb_build_object(
                    'title', latest.title,
                    'stage', 'formed',
                    'weather', 'cloudy',
                    'public_marks', to_jsonb(COALESCE(latest.public_marks, ARRAY[]::text[]))
                  )
                ) AS dest_row,
                latest.npc_id
              FROM (
                SELECT DISTINCT ON (n.npc_id)
                  n.npc_id,
                  n.config_version,
                  n.title,
                  n.public_marks
                FROM public.npc_profiles n
                WHERE n.enabled
                ORDER BY n.npc_id ASC, n.config_version DESC
              ) latest
              ORDER BY latest.npc_id ASC
              LIMIT GREATEST(2 - jsonb_array_length(host_dests), 0)
            ) picked;

            dests := COALESCE(host_dests, '[]'::jsonb) || COALESCE(npc_dests, '[]'::jsonb);
            IF jsonb_array_length(dests) < 1 THEN
              CONTINUE;
            END IF;

            plan := gen_random_uuid();
            FOR idx IN 0 .. jsonb_array_length(dests) - 1 LOOP
              dest_item := dests -> idx;
              INSERT INTO public.visits (
                client_id,
                visitor_spirit_id,
                host_spirit_id,
                npc_id,
                npc_config_version,
                plan_id,
                destination_index,
                status,
                eligibility_key,
                public_context
              ) VALUES (
                gen_random_uuid(),
                visitor.spirit_id,
                CASE
                  WHEN dest_item->>'kind' = 'host' THEN (dest_item->>'host_spirit_id')::uuid
                  ELSE NULL
                END,
                CASE WHEN dest_item->>'kind' = 'npc' THEN dest_item->>'npc_id' ELSE NULL END,
                CASE
                  WHEN dest_item->>'kind' = 'npc' THEN (dest_item->>'npc_config_version')::integer
                  ELSE NULL
                END,
                plan,
                (idx + 1)::smallint,
                'eligible',
                'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || (idx + 1)::text,
                dest_item->'public_context'
              )
              ON CONFLICT ON CONSTRAINT uq_visits_eligibility_key DO NOTHING;
              GET DIAGNOSTICS inserted_one = ROW_COUNT;
              inserted := inserted + inserted_one;
            END LOOP;
          END LOOP;

          RETURN inserted;
        END;
        $$
        """
    )
    op.execute(f"ALTER FUNCTION private.plan_visits(timestamptz) OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION private.plan_visits(timestamptz) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION private.plan_visits(timestamptz) TO kelin_worker")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.plan_visits(timestamptz)")
    op.execute("DROP FUNCTION IF EXISTS private.enqueue_due_visit_plan_jobs(timestamptz)")
    op.execute(f"REVOKE INSERT ON TABLE public.visits FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT ON TABLE public.user_preferences FROM {_DEFINER}")
