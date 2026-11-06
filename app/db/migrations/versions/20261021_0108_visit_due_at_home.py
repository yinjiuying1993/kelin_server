"""Visits settle after the 3-hour away window, then cool down.

Revision ID: 20261021_0108
Revises: 20261021_0107
Create Date: 2026-10-21
"""

from collections.abc import Sequence
import importlib.util
from pathlib import Path

from alembic import op

revision: str = "20261021_0108"
down_revision: str | None = "20261021_0107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"

_INSERT_OLD = """INSERT INTO public.visits (
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
                visitor.spirit_id,"""

_INSERT_NEW = """INSERT INTO public.visits (
                client_id,
                visitor_spirit_id,
                host_spirit_id,
                npc_id,
                npc_config_version,
                plan_id,
                destination_index,
                status,
                due_at,
                eligibility_key,
                public_context
              ) VALUES (
                gen_random_uuid(),
                visitor.spirit_id,"""

_VALUES_TAIL_OLD = """(idx + 1)::smallint,
                'eligible',
                'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || (idx + 1)::text,"""

_VALUES_TAIL_NEW = """(idx + 1)::smallint,
                'eligible',
                COALESCE(visitor.away_until, p_now + interval '3 hours'),
                'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || (idx + 1)::text,"""

_SETTLE_SQL = r"""
CREATE OR REPLACE FUNCTION private.settle_visit(
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
DECLARE
  job uuid;
  visitor uuid;
  host uuid;
  npc text;
  title text;
  visitor_text text;
  host_text text;
  changed integer := 0;
  finished boolean := false;
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
    finished := NOT EXISTS (
      SELECT 1 FROM public.visits inflight
      WHERE inflight.visitor_spirit_id = visitor
        AND inflight.status IN ('eligible', 'visiting')
    );
    PERFORM 1 FROM public.spirits s WHERE s.id = visitor FOR UPDATE;
    UPDATE public.spirits s
    SET
      has_visited = true,
      status = CASE WHEN finished THEN 'home' ELSE s.status END,
      away_until = CASE WHEN finished THEN NULL ELSE s.away_until END,
      version = s.version + 1,
      updated_at = p_now
    WHERE s.id = visitor;
    IF finished THEN
      UPDATE public.spirit_awareness
      SET
        status = 'home',
        away_until = NULL,
        visit_cooldown_until = p_now + interval '12 hours',
        updated_at = p_now
      WHERE spirit_id = visitor;
      UPDATE public.room_projections
      SET
        status = 'home',
        text_status = 'home',
        updated_at = p_now
      WHERE spirit_id = visitor;
    END IF;
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


def _load(name: str) -> object:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"migration source is missing: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plan_with_due_at() -> str:
    previous = _load("20261021_0107_random_visit_hosts.py")
    sql = str(previous._random_plan_sql())
    sql = sql.replace(
        "SELECT s.id AS spirit_id\n",
        "SELECT s.id AS spirit_id, s.away_until AS away_until\n",
    )
    if _INSERT_OLD not in sql or _VALUES_TAIL_OLD not in sql:
        raise RuntimeError("visit plan insert block is missing")
    sql = sql.replace(_INSERT_OLD, _INSERT_NEW, 1).replace(_VALUES_TAIL_OLD, _VALUES_TAIL_NEW, 1)
    if "due_at" not in sql or "interval '3 hours'" not in sql:
        raise RuntimeError("visit plan is not writing due_at")
    return sql


def upgrade() -> None:
    op.execute(f"GRANT SELECT, UPDATE ON TABLE public.spirit_awareness TO {_DEFINER}")
    op.execute(f"GRANT SELECT, UPDATE ON TABLE public.room_projections TO {_DEFINER}")
    op.execute(_plan_with_due_at())
    op.execute(_SETTLE_SQL)
    op.execute(
        "ALTER FUNCTION private.settle_visit(uuid, timestamptz, text, text) "
        f"OWNER TO {_DEFINER}"
    )


def downgrade() -> None:
    previous = _load("20261021_0107_random_visit_hosts.py")
    op.execute(str(previous._random_plan_sql()))
    postcard = _load("20260908_0016_visit_postcard_provider.py")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION private.settle_visit(
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
        + str(postcard._SETTLE_BODY)
        + """
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION private.settle_visit(uuid, timestamptz, text, text) "
        f"OWNER TO {_DEFINER}"
    )
