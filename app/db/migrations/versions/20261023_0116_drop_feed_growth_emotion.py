"""Drop feed, growth, and emotion tables after readers moved off them.

Revision ID: 20261023_0116
Revises: 20261022_0115
Create Date: 2026-10-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261023_0116"
down_revision: str | None = "20261022_0115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"

_GROWTH_INSERT = """
  INSERT INTO public.growth_events (
    spirit_id, source_type, source_id, event_type, payload, occurred_at, applied_at
  ) VALUES (
    visitor, 'visit', p_visit_id, 'visit_completed', '{}'::jsonb, p_now, p_now
  )
  ON CONFLICT (source_type, source_id, event_type) DO NOTHING;
"""

_SETTLE_WITH_GROWTH = """
CREATE OR REPLACE FUNCTION private.settle_visit(p_visit_id uuid, p_now timestamp with time zone, p_visitor_text text, p_host_text text)
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog', 'public', 'private'
AS $function$
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
$function$
"""

_RESTORE_TABLES = """
CREATE TABLE public.feeds (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    spirit_id uuid NOT NULL,
    client_id uuid NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    rejection_code text,
    effect_applied_at timestamp with time zone,
    promise_status text,
    remind_at timestamp with time zone,
    completed_at timestamp with time zone,
    version bigint DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT feeds_pkey PRIMARY KEY (id),
    CONSTRAINT uq_feeds_user_id_client_id UNIQUE (user_id, client_id),
    CONSTRAINT ck_feeds_kind CHECK ((kind = ANY (ARRAY['food'::text, 'sight'::text, 'knowledge'::text, 'emotion'::text, 'promise'::text]))),
    CONSTRAINT ck_feeds_payload_object CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT ck_feeds_promise_status CHECK ((((kind <> 'promise'::text) AND (promise_status IS NULL)) OR ((kind = 'promise'::text) AND (promise_status = ANY (ARRAY['active'::text, 'completed'::text, 'cancelled'::text]))))),
    CONSTRAINT ck_feeds_status CHECK ((status = ANY (ARRAY['pending'::text, 'processing'::text, 'accepted'::text, 'rejected'::text, 'cancelled'::text]))),
    CONSTRAINT feeds_spirit_id_fkey FOREIGN KEY (spirit_id) REFERENCES public.spirits(id) ON DELETE CASCADE,
    CONSTRAINT feeds_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE
);
CREATE INDEX feeds_pending ON public.feeds USING btree (status, updated_at) WHERE (status = ANY (ARRAY['pending'::text, 'processing'::text]));
CREATE UNIQUE INDEX uq_feeds_spirit_one_active_promise ON public.feeds USING btree (spirit_id) WHERE ((kind = 'promise'::text) AND (promise_status = 'active'::text));
CREATE TRIGGER feeds_set_updated_at BEFORE UPDATE ON public.feeds FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER feeds_v1_block_sight BEFORE INSERT OR UPDATE ON public.feeds FOR EACH ROW WHEN ((new.kind = 'sight'::text)) EXECUTE FUNCTION private.v1_block_retired_write();

CREATE TABLE public.growth_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    spirit_id uuid NOT NULL,
    source_type text NOT NULL,
    source_id uuid NOT NULL,
    event_type text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    applied_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    decision_reason text,
    rules_version text DEFAULT 'kelin-v1-rules-1'::text NOT NULL,
    CONSTRAINT growth_events_pkey PRIMARY KEY (id),
    CONSTRAINT uq_growth_events_source_type_source_id_event_type UNIQUE (source_type, source_id, event_type),
    CONSTRAINT ck_growth_events_decision_reason CHECK (((decision_reason IS NULL) OR ((char_length(decision_reason) >= 1) AND (char_length(decision_reason) <= 64)))),
    CONSTRAINT ck_growth_events_event_type CHECK ((event_type = ANY (ARRAY['chat_completed'::text, 'feed_accepted'::text, 'promise_completed'::text, 'pact_answered'::text, 'pact_completed'::text, 'memory_added'::text, 'visit_completed'::text, 'returned_from_lost'::text, 'time_passed'::text]))),
    CONSTRAINT ck_growth_events_payload_object CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT ck_growth_events_rules_version CHECK (((char_length(rules_version) >= 1) AND (char_length(rules_version) <= 64))),
    CONSTRAINT growth_events_spirit_id_fkey FOREIGN KEY (spirit_id) REFERENCES public.spirits(id) ON DELETE CASCADE
);

CREATE TABLE public.spirit_emotion (
    spirit_id uuid NOT NULL,
    mood smallint DEFAULT 60 NOT NULL,
    realtime_emotion text,
    emotion_reason_code text,
    emotion_started_at timestamp with time zone,
    emotion_expires_at timestamp with time zone,
    emotion_source_event_id uuid,
    primary_label text,
    emotion_snapshot_version bigint DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT spirit_emotion_pkey PRIMARY KEY (spirit_id),
    CONSTRAINT ck_spirit_emotion_mood CHECK (((mood >= 0) AND (mood <= 100))),
    CONSTRAINT ck_spirit_emotion_primary_label CHECK (((primary_label IS NULL) OR ((char_length(primary_label) >= 1) AND (char_length(primary_label) <= 32)))),
    CONSTRAINT ck_spirit_emotion_snapshot_version CHECK ((emotion_snapshot_version >= 1)),
    CONSTRAINT spirit_emotion_spirit_id_fkey FOREIGN KEY (spirit_id) REFERENCES public.spirits(id) ON DELETE CASCADE
);
CREATE TRIGGER spirit_emotion_set_updated_at BEFORE UPDATE ON public.spirit_emotion FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

CREATE TABLE public.spirit_growth (
    spirit_id uuid NOT NULL,
    closeness smallint NOT NULL,
    curiosity smallint NOT NULL,
    sharpness smallint NOT NULL,
    nocturnal smallint NOT NULL,
    stubborn smallint NOT NULL,
    bond smallint DEFAULT 0 NOT NULL,
    stage text DEFAULT 'whelp'::text NOT NULL,
    stage_image_key text DEFAULT 'whelp_image'::text NOT NULL,
    cognition smallint DEFAULT 0 NOT NULL,
    has_visited boolean DEFAULT false NOT NULL,
    trait_delta_day date,
    trait_abs_delta_today smallint DEFAULT 0 NOT NULL,
    last_decision_reason text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    familiarity_summaries jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT spirit_growth_pkey PRIMARY KEY (spirit_id),
    CONSTRAINT ck_spirit_growth_cognition CHECK ((cognition >= 0)),
    CONSTRAINT ck_spirit_growth_daily_trait CHECK (((trait_abs_delta_today >= 0) AND (trait_abs_delta_today <= 4))),
    CONSTRAINT ck_spirit_growth_familiarity_summaries CHECK ((jsonb_typeof(familiarity_summaries) = 'array'::text)),
    CONSTRAINT ck_spirit_growth_stage CHECK ((stage = ANY (ARRAY['whelp'::text, 'formed'::text, 'awake'::text]))),
    CONSTRAINT ck_spirit_growth_stage_image_key CHECK ((stage_image_key = ANY (ARRAY['whelp_image'::text, 'formed_image'::text, 'awake_image'::text]))),
    CONSTRAINT ck_spirit_growth_trait_ranges CHECK ((((closeness >= 0) AND (closeness <= 100)) AND ((curiosity >= 0) AND (curiosity <= 100)) AND ((sharpness >= 0) AND (sharpness <= 100)) AND ((nocturnal >= 0) AND (nocturnal <= 100)) AND ((stubborn >= 0) AND (stubborn <= 100)) AND ((bond >= 0) AND (bond <= 100)))),
    CONSTRAINT spirit_growth_spirit_id_fkey FOREIGN KEY (spirit_id) REFERENCES public.spirits(id) ON DELETE CASCADE
);
CREATE TRIGGER spirit_growth_set_updated_at BEFORE UPDATE ON public.spirit_growth FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
"""

_RESTORE_SECURITY = """
ALTER TABLE public.feeds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.feeds FORCE ROW LEVEL SECURITY;
ALTER TABLE public.growth_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.growth_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.spirit_emotion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.spirit_emotion FORCE ROW LEVEL SECURITY;
ALTER TABLE public.spirit_growth ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.spirit_growth FORCE ROW LEVEL SECURITY;

CREATE POLICY rls_feeds_kelin_api_all ON public.feeds TO kelin_api
  USING ((user_id = auth.uid()) AND EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = feeds.spirit_id AND s.user_id = auth.uid()))
  WITH CHECK ((user_id = auth.uid()) AND EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = feeds.spirit_id AND s.user_id = auth.uid()));
CREATE POLICY rls_feeds_kelin_worker_all ON public.feeds TO kelin_worker
  USING ((user_id = auth.uid()) AND EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = feeds.spirit_id AND s.user_id = auth.uid()))
  WITH CHECK ((user_id = auth.uid()) AND EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = feeds.spirit_id AND s.user_id = auth.uid()));
CREATE POLICY rls_growth_events_kelin_api_all ON public.growth_events TO kelin_api
  USING (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = growth_events.spirit_id AND s.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = growth_events.spirit_id AND s.user_id = auth.uid()));
CREATE POLICY rls_growth_events_kelin_worker_all ON public.growth_events TO kelin_worker
  USING (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = growth_events.spirit_id AND s.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = growth_events.spirit_id AND s.user_id = auth.uid()));
CREATE POLICY rls_spirit_emotion_kelin_api_all ON public.spirit_emotion TO kelin_api
  USING (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = spirit_emotion.spirit_id AND s.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = spirit_emotion.spirit_id AND s.user_id = auth.uid()));
CREATE POLICY rls_spirit_emotion_kelin_worker_all ON public.spirit_emotion TO kelin_worker
  USING (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = spirit_emotion.spirit_id AND s.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = spirit_emotion.spirit_id AND s.user_id = auth.uid()));
CREATE POLICY rls_spirit_growth_kelin_api_all ON public.spirit_growth TO kelin_api
  USING (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = spirit_growth.spirit_id AND s.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = spirit_growth.spirit_id AND s.user_id = auth.uid()));
CREATE POLICY rls_spirit_growth_kelin_worker_all ON public.spirit_growth TO kelin_worker
  USING (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = spirit_growth.spirit_id AND s.user_id = auth.uid()))
  WITH CHECK (EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = spirit_growth.spirit_id AND s.user_id = auth.uid()));

GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.feeds TO kelin_api, kelin_worker;
GRANT DELETE ON TABLE public.feeds TO kelin_definer;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.growth_events TO kelin_api, kelin_worker;
GRANT SELECT, INSERT, DELETE ON TABLE public.growth_events TO kelin_definer;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.spirit_emotion TO kelin_api, kelin_worker;
GRANT SELECT, DELETE ON TABLE public.spirit_emotion TO kelin_definer;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.spirit_growth TO kelin_api, kelin_worker;
GRANT SELECT, DELETE ON TABLE public.spirit_growth TO kelin_definer;
"""

_SIGHT_FEED_FUNCTION = """
CREATE FUNCTION public.sight_uploads_require_sight_feed()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  feed_kind text;
BEGIN
  SELECT kind INTO feed_kind FROM public.feeds WHERE id = NEW.feed_id;
  IF feed_kind IS DISTINCT FROM 'sight' THEN
    RAISE EXCEPTION 'sight_uploads.feed_id must reference a sight feed'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$
"""


def _own_settle() -> None:
    op.execute(
        "ALTER FUNCTION private.settle_visit(uuid, timestamptz, text, text) "
        f"OWNER TO {_DEFINER}"
    )


def upgrade() -> None:
    if _GROWTH_INSERT not in _SETTLE_WITH_GROWTH:
        raise RuntimeError("settle_visit growth insert is missing")
    op.execute(_SETTLE_WITH_GROWTH.replace(_GROWTH_INSERT, "\n", 1))
    _own_settle()
    op.execute(
        "DROP TRIGGER IF EXISTS sight_uploads_require_sight_feed ON public.sight_uploads"
    )
    op.execute("DROP FUNCTION IF EXISTS public.sight_uploads_require_sight_feed()")
    op.execute(
        "ALTER TABLE public.memories DROP CONSTRAINT IF EXISTS memories_source_feed_id_fkey"
    )
    op.execute(
        "ALTER TABLE public.sight_uploads DROP CONSTRAINT IF EXISTS sight_uploads_feed_id_fkey"
    )
    op.execute("DROP TABLE IF EXISTS public.feeds")
    op.execute("DROP TABLE IF EXISTS public.growth_events")
    op.execute("DROP TABLE IF EXISTS public.spirit_emotion")
    op.execute("DROP TABLE IF EXISTS public.spirit_growth")


def downgrade() -> None:
    op.execute(_RESTORE_TABLES)
    op.execute(_RESTORE_SECURITY)
    op.execute(_SIGHT_FEED_FUNCTION)
    op.execute(
        """
        CREATE TRIGGER sight_uploads_require_sight_feed
        BEFORE INSERT OR UPDATE OF feed_id ON public.sight_uploads
        FOR EACH ROW EXECUTE FUNCTION public.sight_uploads_require_sight_feed()
        """
    )
    op.execute(
        """
        ALTER TABLE public.memories
        ADD CONSTRAINT memories_source_feed_id_fkey
        FOREIGN KEY (source_feed_id) REFERENCES public.feeds(id) ON DELETE SET NULL
        """
    )
    op.execute(
        """
        ALTER TABLE public.sight_uploads
        ADD CONSTRAINT sight_uploads_feed_id_fkey
        FOREIGN KEY (feed_id) REFERENCES public.feeds(id) ON DELETE CASCADE
        """
    )
    op.execute(_SETTLE_WITH_GROWTH)
    _own_settle()
