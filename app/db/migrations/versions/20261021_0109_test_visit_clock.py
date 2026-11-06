"""DP4RBKLV skips the 18h, 3h, and 12h visit clocks while testing.

Revision ID: 20261021_0109
Revises: 20261021_0108
Create Date: 2026-10-21
"""

from collections.abc import Sequence
import importlib.util
from pathlib import Path

from alembic import op

revision: str = "20261021_0109"
down_revision: str | None = "20261021_0108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"
_TEST_CODE = "DP4RBKLV"

_SKIP_OLD = """            IF EXISTS (
              SELECT 1
              FROM public.visits v
              WHERE v.eligibility_key IN (
                'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || '1',
                'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || '2'
              )
            ) THEN
              CONTINUE;
            END IF;"""

_SKIP_NEW = f"""            IF visitor.invite_code = '{_TEST_CODE}' THEN
              IF EXISTS (
                SELECT 1 FROM public.visits v
                WHERE v.visitor_spirit_id = visitor.spirit_id
                  AND v.status IN ('eligible', 'visiting')
              ) THEN
                CONTINUE;
              END IF;
            ELSIF EXISTS (
              SELECT 1
              FROM public.visits v
              WHERE v.eligibility_key IN (
                'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || '1',
                'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || '2'
              )
            ) THEN
              CONTINUE;
            END IF;"""

_KEY_OLD = (
    "'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || (idx + 1)::text,"
)
_KEY_NEW = (
    "'visit:' || visitor.spirit_id::text || ':' || bucket || ':' || (idx + 1)::text"
    f" || CASE WHEN visitor.invite_code = '{_TEST_CODE}'"
    " THEN ':' || to_char(p_now AT TIME ZONE 'UTC', 'YYYYMMDDHH24MI') ELSE '' END,"
)

_DUE_OLD = "COALESCE(visitor.away_until, p_now + interval '3 hours'),"
_DUE_NEW = (
    "CASE WHEN visitor.invite_code = "
    f"'{_TEST_CODE}' THEN p_now + interval '15 minutes' "
    "ELSE COALESCE(visitor.away_until, p_now + interval '3 hours') END,"
)

_SLOT = """
          to_char(
            date_trunc('hour', p_now AT TIME ZONE 'UTC')
            + make_interval(
              mins => (floor(EXTRACT(MINUTE FROM (p_now AT TIME ZONE 'UTC')) / 15) * 15)::int
            ),
            'YYYYMMDDHH24MI'
          )"""

_PLAN_ENQUEUE = f"""
CREATE OR REPLACE FUNCTION private.enqueue_due_visit_plan_jobs(p_now timestamptz)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $$
DECLARE
  inserted integer := 0;
  bucket text;
  test_slot text;
BEGIN
  IF p_now IS NULL THEN
    RAISE EXCEPTION 'now is required';
  END IF;
  bucket := to_char((p_now AT TIME ZONE 'UTC'), 'YYYYMMDD')
    || CASE
      WHEN EXTRACT(HOUR FROM (p_now AT TIME ZONE 'UTC')) < 12 THEN '00'
      ELSE '12'
    END;
  test_slot := {_SLOT};
  INSERT INTO public.outbox_events (
    aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload, available_at
  )
  SELECT
    'spirit',
    s.id,
    'visit.plan',
    'visit-plan:' || s.id::text || ':' || CASE
      WHEN s.invite_code = '{_TEST_CODE}' THEN test_slot ELSE bucket END,
    s.user_id,
    jsonb_build_object('schedule_bucket', bucket, 'resource_id', s.id),
    p_now
  FROM public.spirits s
  LEFT JOIN public.user_preferences vp ON vp.user_id = s.user_id
  WHERE s.status NOT IN ('lost', 'study')
    AND COALESCE(vp.visit_on, true) IS TRUE
    AND (
      s.invite_code = '{_TEST_CODE}'
      OR s.status = 'away'
      OR s.last_interact_at <= p_now - interval '18 hours'
    )
  ON CONFLICT (dedupe_key) DO NOTHING;
  GET DIAGNOSTICS inserted = ROW_COUNT;
  RETURN inserted;
END;
$$
"""

_STATE_ENQUEUE = f"""
CREATE OR REPLACE FUNCTION private.enqueue_due_state_jobs(p_now timestamptz)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $$
DECLARE
  inserted integer := 0;
  bucket text := to_char((p_now AT TIME ZONE 'UTC'), 'YYYYMMDDHH24');
  test_slot text := {_SLOT};
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
    'state:' || s.id::text || ':' || CASE
      WHEN s.invite_code = '{_TEST_CODE}' THEN test_slot ELSE bucket END,
    s.user_id,
    jsonb_build_object('resource_id', s.id),
    p_now
  FROM public.spirits s
  WHERE s.invite_code = '{_TEST_CODE}'
     OR s.last_interact_at <= p_now - interval '18 hours'
     OR (s.study_until IS NOT NULL AND s.study_until <= p_now)
     OR (s.away_until IS NOT NULL AND s.away_until <= p_now)
  ON CONFLICT (dedupe_key) DO NOTHING;
  GET DIAGNOSTICS inserted = ROW_COUNT;
  RETURN inserted;
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


def _test_plan_sql() -> str:
    previous = _load("20261021_0108_visit_due_at_home.py")
    sql = str(previous._plan_with_due_at())
    replacements = (
        (
            "SELECT s.id AS spirit_id, s.away_until AS away_until\n",
            "SELECT s.id AS spirit_id, s.away_until AS away_until, "
            "s.invite_code AS invite_code\n",
        ),
        (
            "s.status = 'away'\n                OR s.last_interact_at <= p_now - interval '18 hours'",
            f"s.invite_code = '{_TEST_CODE}'\n"
            "                OR s.status = 'away'\n"
            "                OR s.last_interact_at <= p_now - interval '18 hours'",
        ),
        (_SKIP_OLD, _SKIP_NEW),
        (_DUE_OLD, _DUE_NEW),
        (_KEY_OLD, _KEY_NEW),
    )
    for old, new in replacements:
        if old not in sql:
            raise RuntimeError("visit plan test patch missed a block")
        sql = sql.replace(old, new, 1)
    if "interval '15 minutes'" not in sql or _TEST_CODE not in sql:
        raise RuntimeError("visit plan is not opening the test clock")
    return sql


def _test_settle_sql() -> str:
    previous = _load("20261021_0108_visit_due_at_home.py")
    sql = str(previous._SETTLE_SQL)
    old = "visit_cooldown_until = p_now + interval '12 hours',"
    new = (
        "visit_cooldown_until = CASE WHEN EXISTS ("
        "SELECT 1 FROM public.spirits test_spirit "
        "WHERE test_spirit.id = visitor "
        f"AND test_spirit.invite_code = '{_TEST_CODE}'"
        ") THEN NULL ELSE p_now + interval '12 hours' END,"
    )
    if old not in sql:
        raise RuntimeError("settle cooldown assignment is missing")
    return sql.replace(old, new, 1)


def upgrade() -> None:
    op.execute(_test_plan_sql())
    op.execute(_test_settle_sql())
    op.execute(
        "ALTER FUNCTION private.settle_visit(uuid, timestamptz, text, text) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(_PLAN_ENQUEUE)
    op.execute(
        "ALTER FUNCTION private.enqueue_due_visit_plan_jobs(timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(_STATE_ENQUEUE)
    op.execute(
        "ALTER FUNCTION private.enqueue_due_state_jobs(timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        "UPDATE public.spirit_awareness AS awareness "
        "SET visit_cooldown_until = NULL, updated_at = clock_timestamp() "
        "FROM public.spirits AS spirit "
        "WHERE awareness.spirit_id = spirit.id "
        f"AND spirit.invite_code = '{_TEST_CODE}'"
    )


def downgrade() -> None:
    previous = _load("20261021_0108_visit_due_at_home.py")
    op.execute(str(previous._plan_with_due_at()))
    op.execute(str(previous._SETTLE_SQL))
    op.execute(
        "ALTER FUNCTION private.settle_visit(uuid, timestamptz, text, text) "
        f"OWNER TO {_DEFINER}"
    )
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
            'spirit', s.id, 'visit.plan',
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
            'spirit', s.id, 'state.settle',
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
    op.execute(
        "ALTER FUNCTION private.enqueue_due_state_jobs(timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
