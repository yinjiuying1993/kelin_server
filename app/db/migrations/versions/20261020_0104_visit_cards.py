"""Visit message cards: draft, seal on departure, reply, return home.

Revision ID: 20261020_0104
Revises: 20261016_0103
Create Date: 2026-10-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261020_0104"
down_revision: str | None = "20261016_0103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"
_DEFAULT_TEXT = "它来看你了。"


def upgrade() -> None:
    op.create_table(
        "visit_cards",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("spirit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("host_spirit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("visitor_spirit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("voice_object_key", sa.Text(), nullable=True),
        sa.Column("voice_seconds", sa.Integer(), nullable=True),
        sa.Column("image_object_key", sa.Text(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_visit_cards"),
        sa.ForeignKeyConstraint(
            ["spirit_id"], ["spirits.id"], name="fk_visit_cards_spirit_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"], ["visits.id"], name="fk_visit_cards_visit_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["host_spirit_id"],
            ["spirits.id"],
            name="fk_visit_cards_host_spirit_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["visitor_spirit_id"],
            ["spirits.id"],
            name="fk_visit_cards_visitor_spirit_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["visit_cards.id"],
            name="fk_visit_cards_parent_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "role IN ('draft', 'sealed', 'reply')",
            name="ck_visit_cards_role",
        ),
        sa.CheckConstraint(
            "status IN ('empty', 'ready', 'sealed', 'delivered', 'open', 'returned', 'late')",
            name="ck_visit_cards_status",
        ),
        schema="public",
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_visit_cards_draft_spirit
        ON public.visit_cards (spirit_id)
        WHERE role = 'draft'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_visit_cards_sealed_visit
        ON public.visit_cards (visit_id)
        WHERE role = 'sealed'
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_visit_cards_current_reply
        ON public.visit_cards (parent_id)
        WHERE role = 'reply' AND status IN ('open', 'returned')
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_visit_cards_reply_client
        ON public.visit_cards (parent_id, client_id)
        WHERE role = 'reply'
        """
    )
    op.execute("ALTER TABLE public.visit_cards ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.visit_cards FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE public.visit_cards FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON TABLE public.visit_cards "
        "FROM kelin_api, kelin_worker, kelin_scheduler"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.visit_cards TO {_DEFINER}"
    )
    op.execute(_NORMALIZE_SQL)
    op.execute(_SAVE_SQL)
    op.execute(_READ_OWN_SQL)
    op.execute(_READ_SEALED_SQL)
    op.execute(_REPLY_SQL)
    op.execute(_SEAL_SQL)
    op.execute(_RETURN_SQL)
    for name, arg in (
        ("private.normalize_visit_card_content(text, text, integer, text, boolean)", "kelin_definer"),
        ("private.save_visit_card(uuid, text, text, integer, text)", "kelin_api"),
        ("private.read_own_visit_card()", "kelin_api"),
        ("private.read_visit_card(uuid)", "kelin_api"),
        ("private.reply_visit_card(uuid, uuid, text, text, integer, text)", "kelin_api"),
        ("private.seal_visit_card(uuid, uuid, uuid)", "kelin_definer"),
        ("private.return_visit_cards(uuid, timestamptz)", "kelin_definer"),
    ):
        op.execute(f"ALTER FUNCTION {name} OWNER TO {_DEFINER}")
        op.execute(f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {name} TO {arg}")
    op.execute(_SEAL_TRIGGER_SQL)
    op.execute(_RETURN_TRIGGER_SQL)
    op.execute(
        """
        CREATE TRIGGER visits_seal_visit_card
        AFTER INSERT ON public.visits
        FOR EACH ROW EXECUTE FUNCTION private.seal_visit_card_trigger()
        """
    )
    op.execute(
        """
        CREATE TRIGGER visits_return_visit_cards
        AFTER UPDATE OF status ON public.visits
        FOR EACH ROW EXECUTE FUNCTION private.return_visit_cards_trigger()
        """
    )
    op.execute(f"ALTER FUNCTION private.seal_visit_card_trigger() OWNER TO {_DEFINER}")
    op.execute(f"ALTER FUNCTION private.return_visit_cards_trigger() OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION private.seal_visit_card_trigger() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION private.return_visit_cards_trigger() FROM PUBLIC")
    op.execute(_PLAN_VISITS_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS visits_return_visit_cards ON public.visits")
    op.execute("DROP TRIGGER IF EXISTS visits_seal_visit_card ON public.visits")
    op.execute("DROP FUNCTION IF EXISTS private.return_visit_cards_trigger()")
    op.execute("DROP FUNCTION IF EXISTS private.seal_visit_card_trigger()")
    op.execute(_PLAN_VISITS_PREVIOUS_SQL)
    op.execute("DROP FUNCTION IF EXISTS private.return_visit_cards(uuid, timestamptz)")
    op.execute("DROP FUNCTION IF EXISTS private.seal_visit_card(uuid, uuid, uuid)")
    op.execute(
        "DROP FUNCTION IF EXISTS private.reply_visit_card(uuid, uuid, text, text, integer, text)"
    )
    op.execute("DROP FUNCTION IF EXISTS private.read_visit_card(uuid)")
    op.execute("DROP FUNCTION IF EXISTS private.read_own_visit_card()")
    op.execute("DROP FUNCTION IF EXISTS private.save_visit_card(uuid, text, text, integer, text)")
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "private.normalize_visit_card_content(text, text, integer, text, boolean)"
    )
    op.drop_table("visit_cards", schema="public")


_NORMALIZE_SQL = r"""
CREATE FUNCTION private.normalize_visit_card_content(
  p_body text,
  p_voice_key text,
  p_voice_seconds integer,
  p_image_key text,
  p_allow_empty boolean
)
RETURNS TABLE (
  body text,
  voice_object_key text,
  voice_seconds integer,
  image_object_key text,
  content_hash text,
  ready boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  v_body text;
  v_voice text;
  v_image text;
  v_seconds integer;
  v_ready boolean;
BEGIN
  v_body := NULLIF(btrim(COALESCE(p_body, '')), '');
  v_voice := NULLIF(btrim(COALESCE(p_voice_key, '')), '');
  v_image := NULLIF(btrim(COALESCE(p_image_key, '')), '');
  v_seconds := p_voice_seconds;
  IF v_body IS NOT NULL AND char_length(v_body) > 200 THEN
    RAISE EXCEPTION 'CARD_TEXT_REJECTED' USING ERRCODE = 'P0001';
  END IF;
  IF (v_voice IS NULL) <> (v_seconds IS NULL) THEN
    RAISE EXCEPTION 'CARD_VOICE_REJECTED' USING ERRCODE = 'P0001';
  END IF;
  IF v_voice IS NOT NULL AND (
    v_seconds < 1 OR v_seconds > 30 OR char_length(v_voice) > 512
  ) THEN
    RAISE EXCEPTION 'CARD_VOICE_REJECTED' USING ERRCODE = 'P0001';
  END IF;
  IF v_image IS NOT NULL AND (
    char_length(v_image) > 512
    OR lower(v_image) !~ '\.(jpg|jpeg|png|webp)$'
  ) THEN
    RAISE EXCEPTION 'CARD_IMAGE_REJECTED' USING ERRCODE = 'P0001';
  END IF;
  v_ready := v_body IS NOT NULL OR v_voice IS NOT NULL OR v_image IS NOT NULL;
  IF NOT v_ready AND NOT COALESCE(p_allow_empty, false) THEN
    RAISE EXCEPTION 'CARD_EMPTY' USING ERRCODE = 'P0001';
  END IF;
  body := v_body;
  voice_object_key := v_voice;
  voice_seconds := CASE WHEN v_voice IS NULL THEN NULL ELSE v_seconds END;
  image_object_key := v_image;
  content_hash := md5(
    coalesce(v_body, '') || '|' || coalesce(v_voice, '') || '|'
    || coalesce(voice_seconds::text, '') || '|' || coalesce(v_image, '')
  );
  ready := v_ready;
  RETURN NEXT;
END;
$card$
"""

_SAVE_SQL = r"""
CREATE FUNCTION private.save_visit_card(
  p_client_id uuid,
  p_body text,
  p_voice_key text,
  p_voice_seconds integer,
  p_image_key text
)
RETURNS TABLE (
  id uuid,
  status text,
  body text,
  voice_object_key text,
  voice_seconds integer,
  image_object_key text,
  client_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  uid uuid := auth.uid();
  caller uuid;
  v_body text;
  v_voice text;
  v_seconds integer;
  v_image text;
  v_hash text;
  v_ready boolean;
  v_status text;
  existing public.visit_cards%ROWTYPE;
BEGIN
  IF uid IS NULL OR p_client_id IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT s.id INTO caller FROM public.spirits s WHERE s.user_id = uid;
  IF caller IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT
    n.body, n.voice_object_key, n.voice_seconds, n.image_object_key, n.content_hash, n.ready
  INTO v_body, v_voice, v_seconds, v_image, v_hash, v_ready
  FROM private.normalize_visit_card_content(
    p_body, p_voice_key, p_voice_seconds, p_image_key, true
  ) AS n;
  v_status := CASE WHEN v_ready THEN 'ready' ELSE 'empty' END;
  PERFORM pg_advisory_xact_lock(hashtext('visit-card:' || caller::text));
  SELECT * INTO existing
  FROM public.visit_cards
  WHERE role = 'draft' AND spirit_id = caller
  FOR UPDATE;
  IF existing.id IS NOT NULL AND existing.client_id = p_client_id THEN
    IF existing.content_hash IS NOT DISTINCT FROM v_hash THEN
      id := existing.id;
      status := existing.status;
      body := existing.body;
      voice_object_key := existing.voice_object_key;
      voice_seconds := existing.voice_seconds;
      image_object_key := existing.image_object_key;
      client_id := existing.client_id;
      RETURN NEXT;
      RETURN;
    END IF;
    RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE = 'P0001';
  END IF;
  IF existing.id IS NULL THEN
    INSERT INTO public.visit_cards (
      role, spirit_id, client_id, content_hash, body, voice_object_key,
      voice_seconds, image_object_key, is_default, status
    ) VALUES (
      'draft', caller, p_client_id, v_hash, v_body, v_voice,
      v_seconds, v_image, false, v_status
    )
    RETURNING
      visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
      visit_cards.voice_seconds, visit_cards.image_object_key, visit_cards.client_id
    INTO id, status, body, voice_object_key, voice_seconds, image_object_key, client_id;
  ELSE
    UPDATE public.visit_cards
    SET
      client_id = p_client_id,
      content_hash = v_hash,
      body = v_body,
      voice_object_key = v_voice,
      voice_seconds = v_seconds,
      image_object_key = v_image,
      status = v_status,
      updated_at = now()
    WHERE visit_cards.id = existing.id
    RETURNING
      visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
      visit_cards.voice_seconds, visit_cards.image_object_key, visit_cards.client_id
    INTO id, status, body, voice_object_key, voice_seconds, image_object_key, client_id;
  END IF;
  RETURN NEXT;
END;
$card$
"""

_READ_OWN_SQL = r"""
CREATE FUNCTION private.read_own_visit_card()
RETURNS TABLE (
  id uuid,
  status text,
  body text,
  voice_object_key text,
  voice_seconds integer,
  image_object_key text,
  client_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  uid uuid := auth.uid();
  caller uuid;
BEGIN
  IF uid IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT s.id INTO caller FROM public.spirits s WHERE s.user_id = uid;
  IF caller IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  RETURN QUERY
  SELECT c.id, c.status, c.body, c.voice_object_key, c.voice_seconds, c.image_object_key, c.client_id
  FROM public.visit_cards c
  WHERE c.role = 'draft' AND c.spirit_id = caller;
  IF NOT FOUND THEN
    id := NULL;
    status := 'empty';
    body := NULL;
    voice_object_key := NULL;
    voice_seconds := NULL;
    image_object_key := NULL;
    client_id := NULL;
    RETURN NEXT;
  END IF;
END;
$card$
"""

_READ_SEALED_SQL = r"""
CREATE FUNCTION private.read_visit_card(p_card_id uuid)
RETURNS TABLE (
  id uuid,
  status text,
  is_default boolean,
  body text,
  voice_object_key text,
  voice_seconds integer,
  image_object_key text,
  reply_id uuid,
  reply_status text,
  reply_body text,
  reply_voice_object_key text,
  reply_voice_seconds integer,
  reply_image_object_key text,
  reply_client_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  uid uuid := auth.uid();
  caller uuid;
  sealed public.visit_cards%ROWTYPE;
BEGIN
  IF uid IS NULL OR p_card_id IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT s.id INTO caller FROM public.spirits s WHERE s.user_id = uid;
  SELECT * INTO sealed
  FROM public.visit_cards c
  WHERE c.id = p_card_id AND c.role = 'sealed';
  IF caller IS NULL OR sealed.id IS NULL
     OR (caller IS DISTINCT FROM sealed.visitor_spirit_id
         AND caller IS DISTINCT FROM sealed.host_spirit_id) THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  id := sealed.id;
  status := sealed.status;
  is_default := sealed.is_default;
  body := sealed.body;
  voice_object_key := sealed.voice_object_key;
  voice_seconds := sealed.voice_seconds;
  image_object_key := sealed.image_object_key;
  SELECT
    r.id, r.status, r.body, r.voice_object_key, r.voice_seconds, r.image_object_key, r.client_id
  INTO
    reply_id, reply_status, reply_body, reply_voice_object_key,
    reply_voice_seconds, reply_image_object_key, reply_client_id
  FROM public.visit_cards r
  WHERE r.role = 'reply' AND r.parent_id = sealed.id AND r.status IN ('open', 'returned')
  LIMIT 1;
  IF reply_id IS NULL THEN
    SELECT
      r.id, r.status, r.body, r.voice_object_key, r.voice_seconds, r.image_object_key, r.client_id
    INTO
      reply_id, reply_status, reply_body, reply_voice_object_key,
      reply_voice_seconds, reply_image_object_key, reply_client_id
    FROM public.visit_cards r
    WHERE r.role = 'reply' AND r.parent_id = sealed.id AND r.status = 'late'
    ORDER BY r.updated_at DESC, r.id DESC
    LIMIT 1;
  END IF;
  RETURN NEXT;
END;
$card$
"""

_REPLY_SQL = r"""
CREATE FUNCTION private.reply_visit_card(
  p_card_id uuid,
  p_client_id uuid,
  p_body text,
  p_voice_key text,
  p_voice_seconds integer,
  p_image_key text
)
RETURNS TABLE (
  id uuid,
  status text,
  body text,
  voice_object_key text,
  voice_seconds integer,
  image_object_key text,
  client_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  uid uuid := auth.uid();
  caller uuid;
  sealed public.visit_cards%ROWTYPE;
  visit_status text;
  v_body text;
  v_voice text;
  v_seconds integer;
  v_image text;
  v_hash text;
  v_ready boolean;
  existing public.visit_cards%ROWTYPE;
BEGIN
  IF uid IS NULL OR p_card_id IS NULL OR p_client_id IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT s.id INTO caller FROM public.spirits s WHERE s.user_id = uid;
  SELECT * INTO sealed
  FROM public.visit_cards c
  WHERE c.id = p_card_id AND c.role = 'sealed'
  FOR UPDATE;
  IF caller IS NULL OR sealed.id IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  IF caller IS DISTINCT FROM sealed.host_spirit_id
     AND caller IS DISTINCT FROM sealed.visitor_spirit_id THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  IF sealed.host_spirit_id IS NULL THEN
    RAISE EXCEPTION 'VISIT_CARD_CLOSED' USING ERRCODE = 'P0001';
  END IF;
  IF caller IS DISTINCT FROM sealed.host_spirit_id THEN
    RAISE EXCEPTION 'NOT_VISIT_HOST' USING ERRCODE = 'P0001';
  END IF;
  SELECT v.status INTO visit_status
  FROM public.visits v
  WHERE v.id = sealed.visit_id;
  IF visit_status IS NULL OR visit_status NOT IN ('eligible', 'visiting', 'settled') THEN
    RAISE EXCEPTION 'VISIT_CARD_CLOSED' USING ERRCODE = 'P0001';
  END IF;
  SELECT
    n.body, n.voice_object_key, n.voice_seconds, n.image_object_key, n.content_hash, n.ready
  INTO v_body, v_voice, v_seconds, v_image, v_hash, v_ready
  FROM private.normalize_visit_card_content(
    p_body, p_voice_key, p_voice_seconds, p_image_key, false
  ) AS n;
  SELECT * INTO existing
  FROM public.visit_cards r
  WHERE r.role = 'reply' AND r.parent_id = sealed.id AND r.client_id = p_client_id;
  IF existing.id IS NOT NULL THEN
    id := existing.id;
    status := existing.status;
    body := existing.body;
    voice_object_key := existing.voice_object_key;
    voice_seconds := existing.voice_seconds;
    image_object_key := existing.image_object_key;
    client_id := existing.client_id;
    RETURN NEXT;
    RETURN;
  END IF;
  id := NULL;
  IF visit_status = 'settled' THEN
    INSERT INTO public.visit_cards (
      role, parent_id, visit_id, host_spirit_id, visitor_spirit_id, client_id,
      content_hash, body, voice_object_key, voice_seconds, image_object_key,
      is_default, status
    ) VALUES (
      'reply', sealed.id, sealed.visit_id, sealed.host_spirit_id, sealed.visitor_spirit_id,
      p_client_id, v_hash, v_body, v_voice, v_seconds, v_image, false, 'late'
    )
    RETURNING
      visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
      visit_cards.voice_seconds, visit_cards.image_object_key, visit_cards.client_id
    INTO id, status, body, voice_object_key, voice_seconds, image_object_key, client_id;
    RETURN NEXT;
    RETURN;
  END IF;
  id := NULL;
  UPDATE public.visit_cards
  SET
    client_id = p_client_id,
    content_hash = v_hash,
    body = v_body,
    voice_object_key = v_voice,
    voice_seconds = v_seconds,
    image_object_key = v_image,
    updated_at = now()
  WHERE visit_cards.role = 'reply'
    AND visit_cards.parent_id = sealed.id
    AND visit_cards.status = 'open'
  RETURNING
    visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
    visit_cards.voice_seconds, visit_cards.image_object_key, visit_cards.client_id
  INTO id, status, body, voice_object_key, voice_seconds, image_object_key, client_id;
  IF id IS NOT NULL THEN
    RETURN NEXT;
    RETURN;
  END IF;
  INSERT INTO public.visit_cards (
    role, parent_id, visit_id, host_spirit_id, visitor_spirit_id, client_id,
    content_hash, body, voice_object_key, voice_seconds, image_object_key,
    is_default, status
  ) VALUES (
    'reply', sealed.id, sealed.visit_id, sealed.host_spirit_id, sealed.visitor_spirit_id,
    p_client_id, v_hash, v_body, v_voice, v_seconds, v_image, false, 'open'
  )
  RETURNING
    visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
    visit_cards.voice_seconds, visit_cards.image_object_key, visit_cards.client_id
  INTO id, status, body, voice_object_key, voice_seconds, image_object_key, client_id;
  RETURN NEXT;
END;
$card$
"""

_SEAL_SQL = rf"""
CREATE FUNCTION private.seal_visit_card(
  p_visit_id uuid,
  p_visitor_id uuid,
  p_host_id uuid
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  draft_id uuid;
  draft_status text;
  draft_body text;
  draft_voice text;
  draft_seconds integer;
  draft_image text;
  ready boolean;
  new_id uuid;
  v_body text;
  v_voice text;
  v_seconds integer;
  v_image text;
  v_default boolean;
BEGIN
  SELECT id INTO new_id
  FROM public.visit_cards
  WHERE role = 'sealed' AND visit_id = p_visit_id;
  IF new_id IS NOT NULL THEN
    RETURN new_id;
  END IF;
  SELECT c.id, c.status, c.body, c.voice_object_key, c.voice_seconds, c.image_object_key
  INTO draft_id, draft_status, draft_body, draft_voice, draft_seconds, draft_image
  FROM public.visit_cards c
  WHERE c.role = 'draft' AND c.spirit_id = p_visitor_id
  FOR UPDATE;
  ready := p_host_id IS NOT NULL
    AND draft_id IS NOT NULL
    AND draft_status = 'ready'
    AND (
      NULLIF(btrim(COALESCE(draft_body, '')), '') IS NOT NULL
      OR NULLIF(btrim(COALESCE(draft_voice, '')), '') IS NOT NULL
      OR NULLIF(btrim(COALESCE(draft_image, '')), '') IS NOT NULL
    );
  IF ready THEN
    v_body := NULLIF(btrim(draft_body), '');
    v_voice := NULLIF(btrim(draft_voice), '');
    v_seconds := CASE WHEN v_voice IS NULL THEN NULL ELSE draft_seconds END;
    v_image := NULLIF(btrim(draft_image), '');
    v_default := false;
  ELSE
    v_body := '{_DEFAULT_TEXT}';
    v_voice := NULL;
    v_seconds := NULL;
    v_image := NULL;
    v_default := true;
  END IF;
  INSERT INTO public.visit_cards (
    role, visit_id, host_spirit_id, visitor_spirit_id, body, voice_object_key,
    voice_seconds, image_object_key, is_default, status
  ) VALUES (
    'sealed', p_visit_id, p_host_id, p_visitor_id, v_body, v_voice,
    v_seconds, v_image, v_default, 'sealed'
  )
  RETURNING id INTO new_id;
  RETURN new_id;
END;
$card$
"""

_RETURN_SQL = r"""
CREATE FUNCTION private.return_visit_cards(p_visit_id uuid, p_now timestamptz)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  marked integer := 0;
BEGIN
  UPDATE public.visit_cards AS reply
  SET status = 'returned', updated_at = COALESCE(p_now, now())
  WHERE reply.role = 'reply'
    AND reply.status = 'open'
    AND reply.parent_id IN (
      SELECT sealed.id
      FROM public.visit_cards sealed
      WHERE sealed.role = 'sealed' AND sealed.visit_id = p_visit_id
    );
  GET DIAGNOSTICS marked = ROW_COUNT;
  UPDATE public.visit_cards
  SET status = 'delivered', updated_at = COALESCE(p_now, now())
  WHERE visit_cards.role = 'sealed'
    AND visit_cards.visit_id = p_visit_id
    AND visit_cards.status = 'sealed';
  RETURN marked;
END;
$card$
"""

_SEAL_TRIGGER_SQL = r"""
CREATE FUNCTION private.seal_visit_card_trigger()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
BEGIN
  PERFORM private.seal_visit_card(NEW.id, NEW.visitor_spirit_id, NEW.host_spirit_id);
  RETURN NEW;
END;
$card$
"""

_RETURN_TRIGGER_SQL = r"""
CREATE FUNCTION private.return_visit_cards_trigger()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
BEGIN
  IF NEW.status = 'settled' AND OLD.status IS DISTINCT FROM 'settled' THEN
    PERFORM private.return_visit_cards(NEW.id, COALESCE(NEW.settled_at, NEW.updated_at, now()));
  END IF;
  RETURN NEW;
END;
$card$
"""

_HOST_ORDER = """
                (
                  SELECT count(*)::integer
                  FROM public.visits recent
                  WHERE recent.visitor_spirit_id = visitor.spirit_id
                    AND recent.host_spirit_id = peer.id
                    AND recent.created_at >= p_now - interval '30 days'
                    AND recent.status IN ('eligible', 'visiting', 'settled')
                ) ASC,
                f.created_at ASC,
                peer.id ASC"""

_PLAN_VISITS_SQL = (
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
                row_number() OVER (ORDER BY """
    + _HOST_ORDER
    + """
                ) AS ord
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
              ORDER BY """
    + _HOST_ORDER
    + """
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

_PLAN_VISITS_PREVIOUS_SQL = _PLAN_VISITS_SQL.replace(_HOST_ORDER, "f.created_at ASC, peer.id ASC")
