"""Visit-card titles, footprint pages, and read marks.

Revision ID: 20261020_0105
Revises: 20261020_0104
Create Date: 2026-10-20
"""

from collections.abc import Sequence
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "20261020_0105"
down_revision: str | None = "20261020_0104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.add_column("visit_cards", sa.Column("voice_title", sa.Text(), nullable=True))
    op.add_column("visit_cards", sa.Column("image_title", sa.Text(), nullable=True))
    op.add_column("visit_cards", sa.Column("image_caption", sa.Text(), nullable=True))
    op.add_column("visit_cards", sa.Column("reply_title", sa.Text(), nullable=True))
    op.add_column(
        "visit_cards",
        sa.Column("host_read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "visit_cards",
        sa.Column("visitor_read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(_SEAL_SQL)
    op.execute(
        "DROP FUNCTION IF EXISTS private.save_visit_card(uuid, text, text, integer, text)"
    )
    op.execute("DROP FUNCTION IF EXISTS private.read_own_visit_card()")
    op.execute("DROP FUNCTION IF EXISTS private.read_visit_card(uuid)")
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "private.reply_visit_card(uuid, uuid, text, text, integer, text)"
    )
    op.execute(_SAVE_SQL)
    op.execute(_READ_OWN_SQL)
    op.execute(_READ_SEALED_SQL)
    op.execute(_REPLY_SQL)
    op.execute(_LIST_SQL)
    op.execute(_READ_MARK_SQL)
    for name, role in (
        (
            "private.save_visit_card(uuid, text, text, integer, text, text, text, text)",
            "kelin_api",
        ),
        ("private.read_own_visit_card()", "kelin_api"),
        ("private.read_visit_card(uuid)", "kelin_api"),
        (
            "private.reply_visit_card(uuid, uuid, text, text, integer, text, text, text, text, text)",
            "kelin_api",
        ),
        (
            "private.list_visit_footprints(text, timestamptz, uuid, integer)",
            "kelin_api",
        ),
        ("private.mark_visit_card_read(uuid)", "kelin_api"),
        ("private.seal_visit_card(uuid, uuid, uuid)", _DEFINER),
    ):
        op.execute(f"ALTER FUNCTION {name} OWNER TO {_DEFINER}")
        op.execute(f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {name} TO {role}")


def downgrade() -> None:
    previous = _previous_revision()
    op.execute(
        "DROP FUNCTION IF EXISTS private.list_visit_footprints(text, timestamptz, uuid, integer)"
    )
    op.execute("DROP FUNCTION IF EXISTS private.mark_visit_card_read(uuid)")
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "private.save_visit_card(uuid, text, text, integer, text, text, text, text)"
    )
    op.execute("DROP FUNCTION IF EXISTS private.read_own_visit_card()")
    op.execute("DROP FUNCTION IF EXISTS private.read_visit_card(uuid)")
    op.execute(
        "DROP FUNCTION IF EXISTS private.reply_visit_card("
        "uuid, uuid, text, text, integer, text, text, text, text, text)"
    )
    op.execute(previous._SAVE_SQL)
    op.execute(previous._READ_OWN_SQL)
    op.execute(previous._READ_SEALED_SQL)
    op.execute(previous._REPLY_SQL)
    op.execute(previous._SEAL_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    for name, role in (
        ("private.save_visit_card(uuid, text, text, integer, text)", "kelin_api"),
        ("private.read_own_visit_card()", "kelin_api"),
        ("private.read_visit_card(uuid)", "kelin_api"),
        (
            "private.reply_visit_card(uuid, uuid, text, text, integer, text)",
            "kelin_api",
        ),
        ("private.seal_visit_card(uuid, uuid, uuid)", _DEFINER),
    ):
        op.execute(f"ALTER FUNCTION {name} OWNER TO {_DEFINER}")
        op.execute(f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {name} TO {role}")
    op.drop_column("visit_cards", "visitor_read_at")
    op.drop_column("visit_cards", "host_read_at")
    op.drop_column("visit_cards", "reply_title")
    op.drop_column("visit_cards", "image_caption")
    op.drop_column("visit_cards", "image_title")
    op.drop_column("visit_cards", "voice_title")


def _previous_revision() -> object:
    path = Path(__file__).with_name("20261020_0104_visit_cards.py")
    spec = importlib.util.spec_from_file_location("visit_cards_0104", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("previous visit card revision is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LABELS = r"""
  v_voice_title := NULLIF(btrim(COALESCE(p_voice_title, '')), '');
  v_image_title := NULLIF(btrim(COALESCE(p_image_title, '')), '');
  v_image_caption := NULLIF(btrim(COALESCE(p_image_caption, '')), '');
  v_reply_title := NULLIF(btrim(COALESCE(p_reply_title, '')), '');
  IF v_voice IS NULL THEN
    v_voice_title := NULL;
  ELSIF char_length(v_voice_title) > 12 OR v_voice NOT LIKE 'visit-cards/%' THEN
    RAISE EXCEPTION 'CARD_VOICE_REJECTED' USING ERRCODE = 'P0001';
  END IF;
  IF v_image IS NULL THEN
    v_image_title := NULL;
    v_image_caption := NULL;
  ELSIF char_length(COALESCE(v_image_title, '')) > 12
     OR char_length(COALESCE(v_image_caption, '')) > 40
     OR v_image NOT LIKE 'visit-cards/%' THEN
    RAISE EXCEPTION 'CARD_IMAGE_REJECTED' USING ERRCODE = 'P0001';
  END IF;
  IF char_length(COALESCE(v_reply_title, '')) > 12 THEN
    RAISE EXCEPTION 'CARD_TEXT_REJECTED' USING ERRCODE = 'P0001';
  END IF;
  v_hash := md5(
    coalesce(v_body, '') || '|' || coalesce(v_voice, '') || '|'
    || coalesce(v_seconds::text, '') || '|' || coalesce(v_image, '') || '|'
    || coalesce(v_voice_title, '') || '|' || coalesce(v_image_title, '') || '|'
    || coalesce(v_image_caption, '') || '|' || coalesce(v_reply_title, '')
  );
"""

_SAVE_SQL = (
    r"""
CREATE FUNCTION private.save_visit_card(
  p_client_id uuid,
  p_body text,
  p_voice_key text,
  p_voice_seconds integer,
  p_image_key text,
  p_voice_title text,
  p_image_title text,
  p_image_caption text
)
RETURNS TABLE (
  id uuid,
  status text,
  body text,
  voice_object_key text,
  voice_seconds integer,
  voice_title text,
  image_object_key text,
  image_title text,
  image_caption text,
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
  v_voice_title text;
  v_image_title text;
  v_image_caption text;
  v_reply_title text;
  v_hash text;
  v_ready boolean;
  v_status text;
  existing_id uuid;
  existing_client uuid;
  existing_hash text;
BEGIN
  IF uid IS NULL OR p_client_id IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT s.id INTO caller FROM public.spirits s WHERE s.user_id = uid;
  IF caller IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT
    n.body, n.voice_object_key, n.voice_seconds, n.image_object_key, n.ready
  INTO v_body, v_voice, v_seconds, v_image, v_ready
  FROM private.normalize_visit_card_content(
    p_body, p_voice_key, p_voice_seconds, p_image_key, true
  ) AS n;
"""
    + _LABELS.replace("p_reply_title", "NULL::text")
    + r"""
  v_status := CASE WHEN v_ready THEN 'ready' ELSE 'empty' END;
  PERFORM pg_advisory_xact_lock(hashtext('visit-card:' || caller::text));
  SELECT c.id, c.client_id, c.content_hash
  INTO existing_id, existing_client, existing_hash
  FROM public.visit_cards c
  WHERE c.role = 'draft' AND c.spirit_id = caller
  FOR UPDATE;
  IF existing_id IS NOT NULL AND existing_client = p_client_id THEN
    IF existing_hash IS NOT DISTINCT FROM v_hash THEN
      RETURN QUERY
      SELECT c.id, c.status, c.body, c.voice_object_key, c.voice_seconds, c.voice_title,
             c.image_object_key, c.image_title, c.image_caption, c.client_id
      FROM public.visit_cards c
      WHERE c.id = existing_id;
      RETURN;
    END IF;
    RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT' USING ERRCODE = 'P0001';
  END IF;
  IF existing_id IS NULL THEN
    INSERT INTO public.visit_cards (
      role, spirit_id, client_id, content_hash, body, voice_object_key, voice_seconds,
      voice_title, image_object_key, image_title, image_caption, is_default, status
    ) VALUES (
      'draft', caller, p_client_id, v_hash, v_body, v_voice, v_seconds,
      v_voice_title, v_image, v_image_title, v_image_caption, false, v_status
    )
    RETURNING
      visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
      visit_cards.voice_seconds, visit_cards.voice_title, visit_cards.image_object_key,
      visit_cards.image_title, visit_cards.image_caption, visit_cards.client_id
    INTO id, status, body, voice_object_key, voice_seconds, voice_title, image_object_key,
         image_title, image_caption, client_id;
  ELSE
    UPDATE public.visit_cards
    SET
      client_id = p_client_id,
      content_hash = v_hash,
      body = v_body,
      voice_object_key = v_voice,
      voice_seconds = v_seconds,
      voice_title = v_voice_title,
      image_object_key = v_image,
      image_title = v_image_title,
      image_caption = v_image_caption,
      reply_title = NULL,
      status = v_status,
      updated_at = now()
    WHERE visit_cards.id = existing_id
    RETURNING
      visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
      visit_cards.voice_seconds, visit_cards.voice_title, visit_cards.image_object_key,
      visit_cards.image_title, visit_cards.image_caption, visit_cards.client_id
    INTO id, status, body, voice_object_key, voice_seconds, voice_title, image_object_key,
         image_title, image_caption, client_id;
  END IF;
  RETURN NEXT;
END;
$card$
"""
)

_READ_OWN_SQL = r"""
CREATE FUNCTION private.read_own_visit_card()
RETURNS TABLE (
  id uuid,
  status text,
  body text,
  voice_object_key text,
  voice_seconds integer,
  voice_title text,
  image_object_key text,
  image_title text,
  image_caption text,
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
  SELECT c.id, c.status, c.body, c.voice_object_key, c.voice_seconds, c.voice_title,
         c.image_object_key, c.image_title, c.image_caption, c.client_id
  FROM public.visit_cards c
  WHERE c.role = 'draft' AND c.spirit_id = caller;
  IF NOT FOUND THEN
    id := NULL;
    status := 'empty';
    body := NULL;
    voice_object_key := NULL;
    voice_seconds := NULL;
    voice_title := NULL;
    image_object_key := NULL;
    image_title := NULL;
    image_caption := NULL;
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
  official boolean,
  body text,
  voice_object_key text,
  voice_seconds integer,
  voice_title text,
  image_object_key text,
  image_title text,
  image_caption text,
  reply_id uuid,
  reply_status text,
  reply_body text,
  reply_voice_object_key text,
  reply_voice_seconds integer,
  reply_voice_title text,
  reply_image_object_key text,
  reply_image_title text,
  reply_image_caption text,
  reply_title text,
  reply_client_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  uid uuid := auth.uid();
  caller uuid;
  sealed_id uuid;
  sealed_status text;
  sealed_default boolean;
  sealed_host uuid;
  sealed_visitor uuid;
  sealed_body text;
  sealed_voice text;
  sealed_seconds integer;
  sealed_voice_title text;
  sealed_image text;
  sealed_image_title text;
  sealed_image_caption text;
BEGIN
  IF uid IS NULL OR p_card_id IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT s.id INTO caller FROM public.spirits s WHERE s.user_id = uid;
  SELECT
    c.id, c.status, c.is_default, c.host_spirit_id, c.visitor_spirit_id, c.body,
    c.voice_object_key, c.voice_seconds, c.voice_title, c.image_object_key,
    c.image_title, c.image_caption
  INTO
    sealed_id, sealed_status, sealed_default, sealed_host, sealed_visitor, sealed_body,
    sealed_voice, sealed_seconds, sealed_voice_title, sealed_image,
    sealed_image_title, sealed_image_caption
  FROM public.visit_cards c
  WHERE c.id = p_card_id AND c.role = 'sealed';
  IF caller IS NULL OR sealed_id IS NULL
     OR (caller IS DISTINCT FROM sealed_visitor AND caller IS DISTINCT FROM sealed_host) THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  id := sealed_id;
  status := sealed_status;
  is_default := sealed_default;
  official := sealed_host IS NULL;
  body := sealed_body;
  voice_object_key := sealed_voice;
  voice_seconds := sealed_seconds;
  voice_title := sealed_voice_title;
  image_object_key := sealed_image;
  image_title := sealed_image_title;
  image_caption := sealed_image_caption;
  SELECT
    r.id, r.status, r.body, r.voice_object_key, r.voice_seconds, r.voice_title,
    r.image_object_key, r.image_title, r.image_caption, r.reply_title, r.client_id
  INTO
    reply_id, reply_status, reply_body, reply_voice_object_key, reply_voice_seconds,
    reply_voice_title, reply_image_object_key, reply_image_title, reply_image_caption,
    reply_title, reply_client_id
  FROM public.visit_cards r
  WHERE r.role = 'reply' AND r.parent_id = sealed_id AND r.status IN ('open', 'returned')
  LIMIT 1;
  IF reply_id IS NULL THEN
    SELECT
      r.id, r.status, r.body, r.voice_object_key, r.voice_seconds, r.voice_title,
      r.image_object_key, r.image_title, r.image_caption, r.reply_title, r.client_id
    INTO
      reply_id, reply_status, reply_body, reply_voice_object_key, reply_voice_seconds,
      reply_voice_title, reply_image_object_key, reply_image_title, reply_image_caption,
      reply_title, reply_client_id
    FROM public.visit_cards r
    WHERE r.role = 'reply' AND r.parent_id = sealed_id AND r.status = 'late'
    ORDER BY r.updated_at DESC, r.id DESC
    LIMIT 1;
  END IF;
  RETURN NEXT;
END;
$card$
"""

_REPLY_SQL = (
    r"""
CREATE FUNCTION private.reply_visit_card(
  p_card_id uuid,
  p_client_id uuid,
  p_body text,
  p_voice_key text,
  p_voice_seconds integer,
  p_image_key text,
  p_voice_title text,
  p_image_title text,
  p_image_caption text,
  p_reply_title text
)
RETURNS TABLE (
  id uuid,
  status text,
  body text,
  voice_object_key text,
  voice_seconds integer,
  voice_title text,
  image_object_key text,
  image_title text,
  image_caption text,
  reply_title text,
  client_id uuid
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  uid uuid := auth.uid();
  caller uuid;
  sealed_id uuid;
  sealed_host uuid;
  sealed_visitor uuid;
  sealed_visit uuid;
  visit_status text;
  v_body text;
  v_voice text;
  v_seconds integer;
  v_image text;
  v_voice_title text;
  v_image_title text;
  v_image_caption text;
  v_reply_title text;
  v_hash text;
  v_ready boolean;
  existing_id uuid;
BEGIN
  IF uid IS NULL OR p_card_id IS NULL OR p_client_id IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT s.id INTO caller FROM public.spirits s WHERE s.user_id = uid;
  SELECT c.id, c.host_spirit_id, c.visitor_spirit_id, c.visit_id
  INTO sealed_id, sealed_host, sealed_visitor, sealed_visit
  FROM public.visit_cards c
  WHERE c.id = p_card_id AND c.role = 'sealed'
  FOR UPDATE;
  IF caller IS NULL OR sealed_id IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  IF caller IS DISTINCT FROM sealed_host AND caller IS DISTINCT FROM sealed_visitor THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  IF sealed_host IS NULL THEN
    RAISE EXCEPTION 'VISIT_CARD_CLOSED' USING ERRCODE = 'P0001';
  END IF;
  IF caller IS DISTINCT FROM sealed_host THEN
    RAISE EXCEPTION 'NOT_VISIT_HOST' USING ERRCODE = 'P0001';
  END IF;
  SELECT v.status INTO visit_status FROM public.visits v WHERE v.id = sealed_visit;
  IF visit_status IS NULL OR visit_status NOT IN ('eligible', 'visiting', 'settled') THEN
    RAISE EXCEPTION 'VISIT_CARD_CLOSED' USING ERRCODE = 'P0001';
  END IF;
  SELECT
    n.body, n.voice_object_key, n.voice_seconds, n.image_object_key, n.ready
  INTO v_body, v_voice, v_seconds, v_image, v_ready
  FROM private.normalize_visit_card_content(
    p_body, p_voice_key, p_voice_seconds, p_image_key, false
  ) AS n;
"""
    + _LABELS
    + r"""
  SELECT r.id INTO existing_id
  FROM public.visit_cards r
  WHERE r.role = 'reply' AND r.parent_id = sealed_id AND r.client_id = p_client_id;
  IF existing_id IS NOT NULL THEN
    RETURN QUERY
    SELECT r.id, r.status, r.body, r.voice_object_key, r.voice_seconds, r.voice_title,
           r.image_object_key, r.image_title, r.image_caption, r.reply_title, r.client_id
    FROM public.visit_cards r
    WHERE r.id = existing_id;
    RETURN;
  END IF;
  IF visit_status = 'settled' THEN
    INSERT INTO public.visit_cards (
      role, parent_id, visit_id, host_spirit_id, visitor_spirit_id, client_id, content_hash,
      body, voice_object_key, voice_seconds, voice_title, image_object_key, image_title,
      image_caption, reply_title, is_default, status
    ) VALUES (
      'reply', sealed_id, sealed_visit, sealed_host, sealed_visitor, p_client_id, v_hash,
      v_body, v_voice, v_seconds, v_voice_title, v_image, v_image_title,
      v_image_caption, v_reply_title, false, 'late'
    )
    RETURNING
      visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
      visit_cards.voice_seconds, visit_cards.voice_title, visit_cards.image_object_key,
      visit_cards.image_title, visit_cards.image_caption, visit_cards.reply_title,
      visit_cards.client_id
    INTO id, status, body, voice_object_key, voice_seconds, voice_title, image_object_key,
         image_title, image_caption, reply_title, client_id;
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
    voice_title = v_voice_title,
    image_object_key = v_image,
    image_title = v_image_title,
    image_caption = v_image_caption,
    reply_title = v_reply_title,
    updated_at = now()
  WHERE visit_cards.role = 'reply'
    AND visit_cards.parent_id = sealed_id
    AND visit_cards.status = 'open'
  RETURNING
    visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
    visit_cards.voice_seconds, visit_cards.voice_title, visit_cards.image_object_key,
    visit_cards.image_title, visit_cards.image_caption, visit_cards.reply_title,
    visit_cards.client_id
  INTO id, status, body, voice_object_key, voice_seconds, voice_title, image_object_key,
       image_title, image_caption, reply_title, client_id;
  IF id IS NOT NULL THEN
    RETURN NEXT;
    RETURN;
  END IF;
  INSERT INTO public.visit_cards (
    role, parent_id, visit_id, host_spirit_id, visitor_spirit_id, client_id, content_hash,
    body, voice_object_key, voice_seconds, voice_title, image_object_key, image_title,
    image_caption, reply_title, is_default, status
  ) VALUES (
    'reply', sealed_id, sealed_visit, sealed_host, sealed_visitor, p_client_id, v_hash,
    v_body, v_voice, v_seconds, v_voice_title, v_image, v_image_title,
    v_image_caption, v_reply_title, false, 'open'
  )
  RETURNING
    visit_cards.id, visit_cards.status, visit_cards.body, visit_cards.voice_object_key,
    visit_cards.voice_seconds, visit_cards.voice_title, visit_cards.image_object_key,
    visit_cards.image_title, visit_cards.image_caption, visit_cards.reply_title,
    visit_cards.client_id
  INTO id, status, body, voice_object_key, voice_seconds, voice_title, image_object_key,
       image_title, image_caption, reply_title, client_id;
  RETURN NEXT;
END;
$card$
"""
)

_SEAL_SQL = r"""
CREATE OR REPLACE FUNCTION private.seal_visit_card(
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
  draft_voice_title text;
  draft_image text;
  draft_image_title text;
  draft_image_caption text;
  ready boolean;
  new_id uuid;
  v_body text;
  v_voice text;
  v_seconds integer;
  v_voice_title text;
  v_image text;
  v_image_title text;
  v_image_caption text;
  v_default boolean;
BEGIN
  SELECT id INTO new_id
  FROM public.visit_cards
  WHERE role = 'sealed' AND visit_id = p_visit_id;
  IF new_id IS NOT NULL THEN
    RETURN new_id;
  END IF;
  SELECT
    c.id, c.status, c.body, c.voice_object_key, c.voice_seconds, c.voice_title,
    c.image_object_key, c.image_title, c.image_caption
  INTO
    draft_id, draft_status, draft_body, draft_voice, draft_seconds, draft_voice_title,
    draft_image, draft_image_title, draft_image_caption
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
    v_voice_title := CASE WHEN v_voice IS NULL THEN NULL ELSE NULLIF(btrim(COALESCE(draft_voice_title, '')), '') END;
    v_image := NULLIF(btrim(draft_image), '');
    v_image_title := CASE WHEN v_image IS NULL THEN NULL ELSE NULLIF(btrim(COALESCE(draft_image_title, '')), '') END;
    v_image_caption := CASE WHEN v_image IS NULL THEN NULL ELSE NULLIF(btrim(COALESCE(draft_image_caption, '')), '') END;
    v_default := false;
  ELSE
    v_body := '它来看你了。';
    v_voice := NULL;
    v_seconds := NULL;
    v_voice_title := NULL;
    v_image := NULL;
    v_image_title := NULL;
    v_image_caption := NULL;
    v_default := true;
  END IF;
  INSERT INTO public.visit_cards (
    role, visit_id, host_spirit_id, visitor_spirit_id, body, voice_object_key,
    voice_seconds, voice_title, image_object_key, image_title, image_caption,
    is_default, status
  ) VALUES (
    'sealed', p_visit_id, p_host_id, p_visitor_id, v_body, v_voice,
    v_seconds, v_voice_title, v_image, v_image_title, v_image_caption,
    v_default, 'sealed'
  )
  RETURNING id INTO new_id;
  RETURN new_id;
END;
$card$
"""

_LIST_SQL = r"""
CREATE FUNCTION private.list_visit_footprints(
  p_box text,
  p_before timestamptz,
  p_before_id uuid,
  p_limit integer
)
RETURNS TABLE (
  id uuid,
  kind text,
  official boolean,
  unread boolean,
  occurred_at timestamptz,
  peer_title text,
  peer_stage text,
  is_default boolean,
  body text,
  voice_object_key text,
  voice_seconds integer,
  voice_title text,
  image_object_key text,
  image_title text,
  image_caption text,
  reply_title text,
  reply_status text,
  reply_body text
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  uid uuid := auth.uid();
  caller uuid;
BEGIN
  IF uid IS NULL OR p_box NOT IN ('inbox', 'outbox') OR p_limit IS NULL OR p_limit < 1 THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT s.id INTO caller FROM public.spirits s WHERE s.user_id = uid;
  IF caller IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  IF p_box = 'inbox' THEN
    RETURN QUERY
    SELECT
      c.id,
      'visit'::text,
      false,
      c.host_read_at IS NULL,
      c.created_at,
      peer.name,
      CASE WHEN peer.stage IN ('whelp', 'formed', 'awake') THEN peer.stage ELSE 'formed' END,
      c.is_default,
      c.body,
      c.voice_object_key,
      c.voice_seconds,
      c.voice_title,
      c.image_object_key,
      c.image_title,
      c.image_caption,
      reply.reply_title,
      reply.status,
      reply.body
    FROM public.visit_cards c
    JOIN public.spirits peer ON peer.id = c.visitor_spirit_id
    LEFT JOIN LATERAL (
      SELECT r.reply_title, r.status, r.body
      FROM public.visit_cards r
      WHERE r.role = 'reply' AND r.parent_id = c.id AND r.status IN ('open', 'returned')
      LIMIT 1
    ) reply ON true
    WHERE c.role = 'sealed'
      AND c.host_spirit_id = caller
      AND (
        p_before IS NULL
        OR (c.created_at, c.id) < (p_before, p_before_id)
      )
    ORDER BY c.created_at DESC, c.id DESC
    LIMIT p_limit;
    RETURN;
  END IF;
  RETURN QUERY
  SELECT page.id, page.kind, page.official, page.unread, page.occurred_at, page.peer_title,
         page.peer_stage, page.is_default, page.body, page.voice_object_key, page.voice_seconds,
         page.voice_title, page.image_object_key, page.image_title, page.image_caption,
         page.reply_title, page.reply_status, page.reply_body
  FROM (
    SELECT
      c.id,
      'visit'::text AS kind,
      (c.host_spirit_id IS NULL) AS official,
      EXISTS (
        SELECT 1 FROM public.visit_cards r
        WHERE r.role = 'reply' AND r.parent_id = c.id AND r.status = 'returned'
          AND r.visitor_read_at IS NULL
      ) AS unread,
      COALESCE(v.settled_at, c.created_at) AS occurred_at,
      COALESCE(host.name, NULLIF(v.public_context->>'title', ''), '未名') AS peer_title,
      CASE
        WHEN host.stage IN ('whelp', 'formed', 'awake') THEN host.stage
        WHEN v.public_context->>'stage' IN ('whelp', 'formed', 'awake') THEN v.public_context->>'stage'
        ELSE 'formed'
      END AS peer_stage,
      c.is_default,
      c.body,
      c.voice_object_key,
      c.voice_seconds,
      c.voice_title,
      c.image_object_key,
      c.image_title,
      c.image_caption,
      reply.reply_title,
      reply.status AS reply_status,
      reply.body AS reply_body
    FROM public.visit_cards c
    JOIN public.visits v ON v.id = c.visit_id
    LEFT JOIN public.spirits host ON host.id = c.host_spirit_id
    LEFT JOIN LATERAL (
      SELECT r.reply_title, r.status, r.body
      FROM public.visit_cards r
      WHERE r.role = 'reply' AND r.parent_id = c.id AND r.status = 'returned'
      LIMIT 1
    ) reply ON true
    WHERE c.role = 'sealed' AND c.visitor_spirit_id = caller
    UNION ALL
    SELECT
      r.id,
      'late'::text,
      (c.host_spirit_id IS NULL),
      r.visitor_read_at IS NULL,
      r.updated_at,
      COALESCE(host.name, NULLIF(v.public_context->>'title', ''), '未名'),
      CASE
        WHEN host.stage IN ('whelp', 'formed', 'awake') THEN host.stage
        WHEN v.public_context->>'stage' IN ('whelp', 'formed', 'awake') THEN v.public_context->>'stage'
        ELSE 'formed'
      END,
      false,
      r.body,
      r.voice_object_key,
      r.voice_seconds,
      r.voice_title,
      r.image_object_key,
      r.image_title,
      r.image_caption,
      r.reply_title,
      r.status,
      r.body
    FROM public.visit_cards r
    JOIN public.visit_cards c ON c.id = r.parent_id AND c.role = 'sealed'
    JOIN public.visits v ON v.id = c.visit_id
    LEFT JOIN public.spirits host ON host.id = c.host_spirit_id
    WHERE r.role = 'reply' AND r.status = 'late' AND c.visitor_spirit_id = caller
  ) page
  WHERE p_before IS NULL OR (page.occurred_at, page.id) < (p_before, p_before_id)
  ORDER BY page.occurred_at DESC, page.id DESC
  LIMIT p_limit;
END;
$card$
"""

_READ_MARK_SQL = r"""
CREATE FUNCTION private.mark_visit_card_read(p_card_id uuid)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, private
AS $card$
DECLARE
  uid uuid := auth.uid();
  caller uuid;
  sealed_host uuid;
  sealed_visitor uuid;
  card_role text;
  parent uuid;
BEGIN
  IF uid IS NULL OR p_card_id IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  SELECT s.id INTO caller FROM public.spirits s WHERE s.user_id = uid;
  SELECT c.role, c.host_spirit_id, c.visitor_spirit_id, c.parent_id
  INTO card_role, sealed_host, sealed_visitor, parent
  FROM public.visit_cards c
  WHERE c.id = p_card_id;
  IF caller IS NULL OR card_role IS NULL THEN
    RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  IF card_role = 'sealed' AND caller = sealed_host THEN
    UPDATE public.visit_cards
    SET host_read_at = COALESCE(host_read_at, now())
    WHERE id = p_card_id;
    RETURN true;
  END IF;
  IF card_role = 'sealed' AND caller = sealed_visitor THEN
    UPDATE public.visit_cards
    SET visitor_read_at = COALESCE(visitor_read_at, now())
    WHERE role = 'reply' AND parent_id = p_card_id AND status = 'returned';
    RETURN true;
  END IF;
  IF card_role = 'reply' AND caller = sealed_visitor AND parent IS NOT NULL THEN
    UPDATE public.visit_cards
    SET visitor_read_at = COALESCE(visitor_read_at, now())
    WHERE id = p_card_id;
    RETURN true;
  END IF;
  RAISE EXCEPTION 'NOT_FOUND' USING ERRCODE = 'P0002';
END;
$card$
"""
