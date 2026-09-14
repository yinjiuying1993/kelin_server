"""Visit settle worker: independent claim/lease and one-shot status. Spec §§7.4, 8.9, 17.1–17.2.

Revision ID: 20260908_0015
Revises: 20260908_0014
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0015"
down_revision: str | None = "20260908_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute(f"GRANT SELECT, UPDATE ON TABLE public.visits TO {_DEFINER}")
    op.execute(f"GRANT SELECT, UPDATE ON TABLE public.spirits TO {_DEFINER}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE public.postcards TO {_DEFINER}")
    op.execute(f"GRANT SELECT, INSERT ON TABLE public.growth_events TO {_DEFINER}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.outbox_events TO {_DEFINER}")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.enqueue_due_visit_settle_jobs(p_now timestamptz)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          inserted integer := 0;
        BEGIN
          IF p_now IS NULL THEN
            RAISE EXCEPTION 'now is required';
          END IF;
          INSERT INTO public.outbox_events (
            aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload, available_at
          )
          SELECT
            'visit',
            v.id,
            'visit.settle',
            'visit-settle:' || v.id::text,
            visitor.user_id,
            jsonb_build_object('resource_id', v.id),
            p_now
          FROM public.visits v
          JOIN public.spirits visitor ON visitor.id = v.visitor_spirit_id
          WHERE v.status IN ('eligible', 'visiting')
            AND (v.due_at IS NULL OR v.due_at <= p_now)
          ON CONFLICT (dedupe_key) DO NOTHING;
          GET DIAGNOSTICS inserted = ROW_COUNT;
          RETURN inserted;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.enqueue_due_visit_settle_jobs(timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.enqueue_due_visit_settle_jobs(timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.enqueue_due_visit_settle_jobs(timestamptz) TO kelin_scheduler"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.claim_due_visit_settle_jobs(
          p_now timestamptz,
          p_limit integer,
          p_worker_id text,
          p_lease_seconds integer
        )
        RETURNS TABLE (
          job_id uuid,
          visit_id uuid,
          owner_id uuid,
          dedupe_key text,
          locked_by text
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          batch integer;
          lease integer;
        BEGIN
          IF p_now IS NULL OR p_worker_id IS NULL OR btrim(p_worker_id) = '' THEN
            RAISE EXCEPTION 'claim inputs are required';
          END IF;
          batch := LEAST(GREATEST(COALESCE(p_limit, 0), 1), 50);
          lease := GREATEST(COALESCE(p_lease_seconds, 120), 1);
          RETURN QUERY
          WITH picked AS (
            SELECT o.id
            FROM public.outbox_events o
            WHERE o.event_type = 'visit.settle'
              AND o.status IN ('pending', 'retry')
              AND o.available_at <= p_now
              AND o.dedupe_key LIKE 'visit-settle:%'
            ORDER BY o.available_at ASC, o.created_at ASC, o.id ASC
            FOR UPDATE OF o SKIP LOCKED
            LIMIT batch
          )
          UPDATE public.outbox_events AS o
          SET
            status = 'claimed',
            locked_by = p_worker_id,
            locked_at = p_now,
            lease_expires_at = p_now + make_interval(secs => lease),
            attempt_count = o.attempt_count + 1
          FROM picked
          WHERE o.id = picked.id
          RETURNING o.id, o.aggregate_id, o.owner_id, o.dedupe_key, o.locked_by;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.claim_due_visit_settle_jobs("
        "timestamptz, integer, text, integer) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.claim_due_visit_settle_jobs("
        "timestamptz, integer, text, integer) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.claim_due_visit_settle_jobs("
        "timestamptz, integer, text, integer) TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.reclaim_expired_outbox(p_now timestamptz)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          updated integer := 0;
        BEGIN
          IF p_now IS NULL THEN
            RAISE EXCEPTION 'now is required';
          END IF;
          UPDATE public.outbox_events
          SET
            status = CASE
              WHEN attempt_count >= max_attempts THEN 'dead'
              ELSE 'retry'
            END,
            locked_by = NULL,
            locked_at = NULL,
            lease_expires_at = NULL,
            last_error_code = CASE
              WHEN attempt_count >= max_attempts THEN COALESCE(last_error_code, 'LEASE_EXPIRED')
              ELSE 'LEASE_EXPIRED'
            END,
            available_at = CASE
              WHEN attempt_count >= max_attempts THEN available_at
              ELSE p_now + make_interval(
                secs => LEAST(300, POWER(2, GREATEST(attempt_count, 1))::integer)
              )
            END
          WHERE status = 'claimed'
            AND lease_expires_at IS NOT NULL
            AND lease_expires_at < p_now;
          GET DIAGNOSTICS updated = ROW_COUNT;
          RETURN updated;
        END;
        $$
        """
    )
    op.execute(
        f"ALTER FUNCTION private.reclaim_expired_outbox(timestamptz) OWNER TO {_DEFINER}"
    )
    op.execute("REVOKE ALL ON FUNCTION private.reclaim_expired_outbox(timestamptz) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.reclaim_expired_outbox(timestamptz) "
        "TO kelin_scheduler, kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.sync_dead_visit_settles(p_now timestamptz)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          updated integer := 0;
          visitor uuid;
        BEGIN
          IF p_now IS NULL THEN
            RAISE EXCEPTION 'now is required';
          END IF;
          FOR visitor IN
            UPDATE public.visits AS v
            SET
              status = 'failed',
              last_error_code = COALESCE(v.last_error_code, 'OUTBOX_DEAD'),
              updated_at = p_now
            FROM public.outbox_events o
            WHERE o.event_type = 'visit.settle'
              AND o.dedupe_key = 'visit-settle:' || v.id::text
              AND o.status = 'dead'
              AND v.status IN ('eligible', 'visiting')
            RETURNING v.visitor_spirit_id
          LOOP
            updated := updated + 1;
            PERFORM 1 FROM public.spirits s WHERE s.id = visitor FOR UPDATE;
            UPDATE public.spirits s
            SET
              has_visited = true,
              status = CASE
                WHEN s.status = 'away'
                  AND NOT EXISTS (
                    SELECT 1 FROM public.visits inflight
                    WHERE inflight.visitor_spirit_id = s.id
                      AND inflight.status IN ('eligible', 'visiting')
                  )
                THEN 'home'
                ELSE s.status
              END,
              away_until = CASE
                WHEN s.status = 'away'
                  AND NOT EXISTS (
                    SELECT 1 FROM public.visits inflight
                    WHERE inflight.visitor_spirit_id = s.id
                      AND inflight.status IN ('eligible', 'visiting')
                  )
                THEN NULL
                ELSE s.away_until
              END,
              version = s.version + 1,
              updated_at = p_now
            WHERE s.id = visitor;
          END LOOP;
          RETURN updated;
        END;
        $$
        """
    )
    op.execute(
        f"ALTER FUNCTION private.sync_dead_visit_settles(timestamptz) OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.sync_dead_visit_settles(timestamptz) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.sync_dead_visit_settles(timestamptz) TO kelin_worker"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.settle_visit(p_visit_id uuid, p_now timestamptz)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        DECLARE
          job uuid;
          visitor uuid;
          host uuid;
          npc text;
          title text;
          visitor_text text;
          host_text text;
          changed integer := 0;
        BEGIN
          IF p_visit_id IS NULL OR p_now IS NULL THEN
            RAISE EXCEPTION 'visit_id and now are required';
          END IF;
          SELECT o.id INTO job
          FROM public.outbox_events o
          WHERE o.event_type = 'visit.settle'
            AND o.dedupe_key = 'visit-settle:' || p_visit_id::text
            AND o.status = 'claimed'
            AND (o.lease_expires_at IS NULL OR o.lease_expires_at > p_now)
          FOR UPDATE;
          IF job IS NULL THEN
            RAISE EXCEPTION 'VISIT_SETTLE_NOT_CLAIMED' USING ERRCODE = 'P0001';
          END IF;

          SELECT v.visitor_spirit_id, v.host_spirit_id, v.npc_id,
                 COALESCE(NULLIF(v.public_context->>'title', ''), '未名')
          INTO visitor, host, npc, title
          FROM public.visits v
          WHERE v.id = p_visit_id
          FOR UPDATE;
          IF visitor IS NULL THEN
            RAISE EXCEPTION 'VISIT_NOT_FOUND' USING ERRCODE = 'P0002';
          END IF;

          UPDATE public.visits
          SET
            status = 'settled',
            started_at = COALESCE(started_at, p_now),
            settled_at = p_now,
            last_error_code = NULL,
            updated_at = p_now
          WHERE id = p_visit_id
            AND status IN ('eligible', 'visiting');
          GET DIAGNOSTICS changed = ROW_COUNT;

          visitor_text := '去过' || title || '，带回一张字条。';
          host_text := '有客人来过。';
          IF npc IS NOT NULL THEN
            INSERT INTO public.postcards (
              visit_id, sender_spirit_id, receiver_spirit_id, npc_id, text
            ) VALUES (
              p_visit_id, NULL, visitor, npc, visitor_text
            )
            ON CONFLICT ON CONSTRAINT uq_postcards_visit_id_receiver_spirit_id DO NOTHING;
          ELSE
            INSERT INTO public.postcards (
              visit_id, sender_spirit_id, receiver_spirit_id, npc_id, text
            ) VALUES (
              p_visit_id, host, visitor, NULL, visitor_text
            )
            ON CONFLICT ON CONSTRAINT uq_postcards_visit_id_receiver_spirit_id DO NOTHING;
            IF host IS NOT NULL THEN
              INSERT INTO public.postcards (
                visit_id, sender_spirit_id, receiver_spirit_id, npc_id, text
              ) VALUES (
                p_visit_id, visitor, host, NULL, host_text
              )
              ON CONFLICT ON CONSTRAINT uq_postcards_visit_id_receiver_spirit_id DO NOTHING;
            END IF;
          END IF;

          INSERT INTO public.growth_events (
            spirit_id, source_type, source_id, event_type, payload, occurred_at, applied_at
          ) VALUES (
            visitor, 'visit', p_visit_id, 'visit_completed', '{}'::jsonb, p_now, p_now
          )
          ON CONFLICT (source_type, source_id, event_type) DO NOTHING;

          IF changed > 0 THEN
            PERFORM 1 FROM public.spirits s WHERE s.id = visitor FOR UPDATE;
            UPDATE public.spirits s
            SET
              has_visited = true,
              status = CASE
                WHEN s.status = 'away'
                  AND NOT EXISTS (
                    SELECT 1 FROM public.visits inflight
                    WHERE inflight.visitor_spirit_id = s.id
                      AND inflight.status IN ('eligible', 'visiting')
                  )
                THEN 'home'
                ELSE s.status
              END,
              away_until = CASE
                WHEN s.status = 'away'
                  AND NOT EXISTS (
                    SELECT 1 FROM public.visits inflight
                    WHERE inflight.visitor_spirit_id = s.id
                      AND inflight.status IN ('eligible', 'visiting')
                  )
                THEN NULL
                ELSE s.away_until
              END,
              version = s.version + 1,
              updated_at = p_now
            WHERE s.id = visitor;
            IF host IS NOT NULL THEN
              PERFORM 1 FROM public.spirits s WHERE s.id = host FOR UPDATE;
              UPDATE public.spirits
              SET version = version + 1, updated_at = p_now
              WHERE id = host;
            END IF;
          END IF;

          UPDATE public.outbox_events
          SET status = 'done', processed_at = p_now, last_error_code = NULL
          WHERE id = job
            AND status = 'claimed';
          RETURN changed;
        END;
        $$
        """
    )
    op.execute(
        f"ALTER FUNCTION private.settle_visit(uuid, timestamptz) OWNER TO {_DEFINER}"
    )
    op.execute("REVOKE ALL ON FUNCTION private.settle_visit(uuid, timestamptz) FROM PUBLIC")
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.settle_visit(uuid, timestamptz) TO kelin_worker"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.settle_visit(uuid, timestamptz)")
    op.execute("DROP FUNCTION IF EXISTS private.sync_dead_visit_settles(timestamptz)")
    op.execute("DROP FUNCTION IF EXISTS private.reclaim_expired_outbox(timestamptz)")
    op.execute(
        "DROP FUNCTION IF EXISTS private.claim_due_visit_settle_jobs("
        "timestamptz, integer, text, integer)"
    )
    op.execute("DROP FUNCTION IF EXISTS private.enqueue_due_visit_settle_jobs(timestamptz)")
    op.execute(f"REVOKE UPDATE ON TABLE public.visits FROM {_DEFINER}")
    op.execute(f"REVOKE UPDATE ON TABLE public.spirits FROM {_DEFINER}")
    op.execute(f"REVOKE INSERT ON TABLE public.postcards FROM {_DEFINER}")
    op.execute(f"REVOKE SELECT, INSERT ON TABLE public.growth_events FROM {_DEFINER}")
    op.execute(f"REVOKE UPDATE ON TABLE public.outbox_events FROM {_DEFINER}")
