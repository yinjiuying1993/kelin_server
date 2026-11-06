"""Plan visits when the test spirit is set away from the console.

Revision ID: 20261021_0111
Revises: 20261021_0110
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261021_0111"
down_revision: str | None = "20261021_0110"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"
_TEST_CODE = "DP4RBKLV"

_PLAN_SQL = f"""
CREATE FUNCTION private.plan_manual_departure(p_spirit_id uuid, p_now timestamptz)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $$
DECLARE
  inserted integer := 0;
  inserted_one integer := 0;
  invite text;
  away_status text;
  away_until timestamptz;
  dests jsonb;
  dest_item jsonb;
  idx integer;
  plan uuid;
  stamp text;
BEGIN
  IF p_spirit_id IS NULL OR p_now IS NULL THEN
    RETURN 0;
  END IF;
  SELECT spirit.invite_code, awareness.status, awareness.away_until
  INTO invite, away_status, away_until
  FROM public.spirits AS spirit
  JOIN public.spirit_awareness AS awareness ON awareness.spirit_id = spirit.id
  WHERE spirit.id = p_spirit_id;
  IF invite IS DISTINCT FROM '{_TEST_CODE}' OR away_status IS DISTINCT FROM 'away' THEN
    RETURN 0;
  END IF;

  PERFORM pg_advisory_xact_lock(hashtext('visit-plan:' || p_spirit_id::text));
  IF EXISTS (
    SELECT 1
    FROM public.visits AS inflight
    WHERE inflight.visitor_spirit_id = p_spirit_id
      AND inflight.status IN ('eligible', 'visiting')
  ) THEN
    RETURN 0;
  END IF;

  SELECT COALESCE(jsonb_agg(ranked.dest_row ORDER BY ranked.ord), '[]'::jsonb)
  INTO dests
  FROM (
    SELECT
      jsonb_build_object(
        'host_spirit_id', peer.id,
        'public_context', jsonb_build_object(
          'title', peer.name,
          'stage', peer.stage,
          'weather', 'cloudy',
          'public_marks', to_jsonb(COALESCE(peer.scholar_marks, ARRAY[]::text[]))
        )
      ) AS dest_row,
      row_number() OVER () AS ord
    FROM (
      SELECT peer.id, peer.name, peer.stage, peer.scholar_marks
      FROM public.spirits AS peer
      LEFT JOIN public.user_preferences AS preference ON preference.user_id = peer.user_id
      WHERE peer.id <> p_spirit_id
        AND peer.status <> 'lost'
        AND COALESCE(preference.visit_on, true) IS TRUE
      ORDER BY random()
      LIMIT 2
    ) AS peer
  ) AS ranked;
  IF dests IS NULL OR jsonb_array_length(dests) < 1 THEN
    RETURN 0;
  END IF;

  plan := gen_random_uuid();
  stamp := to_char(
    (COALESCE(away_until, p_now) AT TIME ZONE 'UTC'),
    'YYYYMMDDHH24MISS'
  );
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
      due_at,
      eligibility_key,
      public_context
    ) VALUES (
      gen_random_uuid(),
      p_spirit_id,
      (dest_item->>'host_spirit_id')::uuid,
      NULL,
      NULL,
      plan,
      (idx + 1)::smallint,
      'eligible',
      COALESCE(away_until, p_now + interval '3 hours'),
      'visit:' || p_spirit_id::text || ':manual:' || stamp || ':' || (idx + 1)::text,
      dest_item->'public_context'
    )
    ON CONFLICT ON CONSTRAINT uq_visits_eligibility_key DO NOTHING;
    GET DIAGNOSTICS inserted_one = ROW_COUNT;
    inserted := inserted + inserted_one;
  END LOOP;
  RETURN inserted;
END;
$$
"""

_TRIGGER_FN_SQL = """
CREATE FUNCTION private.plan_manual_departure_trigger()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $$
BEGIN
  PERFORM private.plan_manual_departure(NEW.spirit_id, clock_timestamp());
  RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    op.execute(_PLAN_SQL)
    op.execute(_TRIGGER_FN_SQL)
    op.execute(
        "ALTER FUNCTION private.plan_manual_departure(uuid, timestamptz) "
        f"OWNER TO {_DEFINER}"
    )
    op.execute(
        f"ALTER FUNCTION private.plan_manual_departure_trigger() OWNER TO {_DEFINER}"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.plan_manual_departure(uuid, timestamptz) FROM PUBLIC"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION private.plan_manual_departure_trigger() FROM PUBLIC"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION private.plan_manual_departure(uuid, timestamptz) TO {_DEFINER}")
    op.execute(
        """
        CREATE TRIGGER awareness_plan_manual_departure
        AFTER INSERT OR UPDATE OF status, away_until
        ON public.spirit_awareness
        FOR EACH ROW
        WHEN (NEW.status = 'away')
        EXECUTE FUNCTION private.plan_manual_departure_trigger()
        """
    )
    op.execute(
        "SELECT private.plan_manual_departure(spirit.id, clock_timestamp()) "
        "FROM public.spirits AS spirit "
        f"WHERE spirit.invite_code = '{_TEST_CODE}'"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS awareness_plan_manual_departure ON public.spirit_awareness"
    )
    op.execute("DROP FUNCTION IF EXISTS private.plan_manual_departure_trigger()")
    op.execute("DROP FUNCTION IF EXISTS private.plan_manual_departure(uuid, timestamptz)")
