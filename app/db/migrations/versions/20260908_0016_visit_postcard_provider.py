"""Visit postcard Provider public input and settle texts. Spec §§8.9, 16.2.

Revision ID: 20260908_0016
Revises: 20260908_0015
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260908_0016"
down_revision: str | None = "20260908_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"

_SETTLE_BODY = r"""
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

          visitor_text := COALESCE(NULLIF(btrim(p_visitor_text), ''), '去过' || title || '，带回一张字条。');
          host_text := COALESCE(NULLIF(btrim(p_host_text), ''), '有客人来过。');
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
"""


def upgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.settle_visit(uuid, timestamptz)")
    op.execute(
        """
        CREATE FUNCTION private.visit_postcard_public_input(p_visit_id uuid)
        RETURNS TABLE (
          visit_id uuid,
          visitor_id uuid,
          host_id uuid,
          npc_id text,
          dest_title text,
          dest_stage text,
          dest_weather text,
          dest_marks jsonb,
          visitor_title text,
          visitor_stage text,
          visitor_weather text,
          visitor_marks jsonb
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        BEGIN
          IF p_visit_id IS NULL THEN
            RAISE EXCEPTION 'visit_id is required';
          END IF;
          IF NOT EXISTS (
            SELECT 1
            FROM public.outbox_events o
            WHERE o.event_type = 'visit.settle'
              AND o.dedupe_key = 'visit-settle:' || p_visit_id::text
              AND o.status = 'claimed'
          ) THEN
            RAISE EXCEPTION 'VISIT_SETTLE_NOT_CLAIMED' USING ERRCODE = 'P0001';
          END IF;
          RETURN QUERY
          SELECT
            v.id,
            v.visitor_spirit_id,
            v.host_spirit_id,
            v.npc_id,
            COALESCE(NULLIF(v.public_context->>'title', ''), '未名'),
            COALESCE(NULLIF(v.public_context->>'stage', ''), 'whelp'),
            COALESCE(NULLIF(v.public_context->>'weather', ''), 'cloudy'),
            COALESCE(v.public_context->'public_marks', '[]'::jsonb),
            COALESCE(NULLIF(visitor.name, ''), '未名'),
            visitor.stage::text,
            'cloudy'::text,
            to_jsonb(COALESCE(visitor.scholar_marks, ARRAY[]::text[]))
          FROM public.visits v
          JOIN public.spirits visitor ON visitor.id = v.visitor_spirit_id
          WHERE v.id = p_visit_id;
        END;
        $$
        """
    )
    op.execute(
        f"ALTER FUNCTION private.visit_postcard_public_input(uuid) OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.visit_postcard_public_input(uuid) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.visit_postcard_public_input(uuid) TO kelin_worker"
    )
    op.execute(
        """
        CREATE FUNCTION private.settle_visit(
          p_visit_id uuid,
          p_now timestamptz,
          p_visitor_text text,
          p_host_text text
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public, private
        AS $$
        """
        + _SETTLE_BODY
        + """
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.settle_visit(uuid, timestamptz, text, text) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.settle_visit(uuid, timestamptz, text, text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION private.settle_visit(uuid, timestamptz, text, text) "
        "TO kelin_worker"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS private.settle_visit(uuid, timestamptz, text, text)")
    op.execute("DROP FUNCTION IF EXISTS private.visit_postcard_public_input(uuid)")
    op.execute(
        """
        CREATE FUNCTION private.settle_visit(p_visit_id uuid, p_now timestamptz)
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
