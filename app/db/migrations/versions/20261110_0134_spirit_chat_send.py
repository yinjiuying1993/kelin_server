"""One definer function commits a spirit-chat send for both participants.

Revision ID: 20261110_0134
Revises: 20261110_0133
Create Date: 2026-11-10

The sender's transaction may write the peer's participant row and outbox row.
Ordinary policies stay owner-scoped. This function is the only cross-user write.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261110_0134"
down_revision: str | None = "20261110_0133"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"
_TABLES = (
    "spirit_chat_threads",
    "spirit_chat_participants",
    "spirit_chat_messages",
    "spirit_chat_outbox",
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{table} TO {_DEFINER}"
        )
    op.execute(
        """
        CREATE FUNCTION public.v4_spirit_chat_commit_send(
          own_spirit uuid,
          peer_spirit uuid,
          body text,
          peer_name text,
          peer_avatar text,
          peer_available boolean,
          own_name text,
          own_avatar text
        )
        RETURNS TABLE (
          thread_id uuid,
          message_id uuid,
          seq integer,
          created_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
          low_id uuid;
          high_id uuid;
          tid uuid;
          mid uuid;
          next_seq integer;
          written timestamptz;
          sender uuid;
          peer_user uuid;
          sender_unread integer;
          peer_unread integer;
        BEGIN
          sender := auth.uid();
          IF sender IS NULL OR own_spirit = peer_spirit THEN
            RAISE EXCEPTION 'not found';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM public.spirits
            WHERE id = own_spirit AND user_id = sender
          ) THEN
            RAISE EXCEPTION 'not found';
          END IF;
          SELECT user_id INTO peer_user FROM public.spirits WHERE id = peer_spirit;
          IF peer_user IS NULL OR peer_user = sender THEN
            RAISE EXCEPTION 'not found';
          END IF;
          low_id := least(own_spirit, peer_spirit);
          high_id := greatest(own_spirit, peer_spirit);
          INSERT INTO public.spirit_chat_threads (spirit_low_id, spirit_high_id)
          VALUES (low_id, high_id)
          ON CONFLICT (spirit_low_id, spirit_high_id) DO NOTHING;
          SELECT id INTO tid
          FROM public.spirit_chat_threads
          WHERE spirit_low_id = low_id AND spirit_high_id = high_id
          FOR UPDATE;
          SELECT COALESCE(MAX(m.seq), 0) + 1 INTO next_seq
          FROM public.spirit_chat_messages AS m
          WHERE m.thread_id = tid;
          INSERT INTO public.spirit_chat_messages (
            thread_id, seq, sender_spirit_id, body
          )
          VALUES (tid, next_seq, own_spirit, body)
          RETURNING id, created_at INTO mid, written;
          INSERT INTO public.spirit_chat_participants (
            thread_id, user_id, my_spirit_id, peer_spirit_id,
            clear_seq, last_read_seq, last_visible_seq,
            peer_display_name, peer_avatar_url, peer_available
          )
          VALUES
            (tid, sender, own_spirit, peer_spirit, 0, next_seq, next_seq,
             peer_name, peer_avatar, peer_available),
            (tid, peer_user, peer_spirit, own_spirit, 0, 0, next_seq,
             own_name, own_avatar, true)
          ON CONFLICT (thread_id, user_id) DO UPDATE SET
            last_visible_seq = EXCLUDED.last_visible_seq,
            peer_display_name = EXCLUDED.peer_display_name,
            peer_avatar_url = EXCLUDED.peer_avatar_url,
            peer_available = EXCLUDED.peer_available,
            last_read_seq = CASE
              WHEN spirit_chat_participants.user_id = sender THEN EXCLUDED.last_read_seq
              ELSE spirit_chat_participants.last_read_seq
            END;
          SELECT count(*)::int INTO sender_unread
          FROM public.spirit_chat_messages AS m
          JOIN public.spirit_chat_participants AS mine
            ON mine.thread_id = tid AND mine.user_id = sender
          WHERE m.thread_id = tid
            AND m.sender_spirit_id <> mine.my_spirit_id
            AND m.seq > mine.last_read_seq
            AND m.seq > mine.clear_seq;
          SELECT count(*)::int INTO peer_unread
          FROM public.spirit_chat_messages AS m
          JOIN public.spirit_chat_participants AS mine
            ON mine.thread_id = tid AND mine.user_id = peer_user
          WHERE m.thread_id = tid
            AND m.sender_spirit_id <> mine.my_spirit_id
            AND m.seq > mine.last_read_seq
            AND m.seq > mine.clear_seq;
          INSERT INTO public.spirit_chat_outbox (
            recipient_user_id, thread_id, event_type, seq, event_body
          )
          VALUES
            (peer_user, tid, 'message.created', next_seq,
             jsonb_build_object(
               'message_id', mid, 'seq', next_seq, 'text', body, 'mine', false
             )::text),
            (peer_user, tid, 'thread.updated', next_seq,
             jsonb_build_object(
               'text', body, 'unread', peer_unread, 'peer_spirit_id', own_spirit,
               'display_name', own_name, 'avatar_url', own_avatar,
               'available', true, 'hidden', false
             )::text),
            (sender, tid, 'thread.updated', next_seq,
             jsonb_build_object(
               'text', body, 'unread', sender_unread, 'peer_spirit_id', peer_spirit,
               'display_name', peer_name, 'avatar_url', peer_avatar,
               'available', peer_available, 'hidden', false
             )::text);
          thread_id := tid;
          message_id := mid;
          seq := next_seq;
          created_at := written;
          RETURN NEXT;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION public.v4_spirit_chat_commit_send("
        "uuid, uuid, text, text, text, boolean, text, text) OWNER TO kelin_definer"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_spirit_chat_commit_send("
        "uuid, uuid, text, text, text, boolean, text, text) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.v4_spirit_chat_commit_send("
        "uuid, uuid, text, text, text, boolean, text, text) TO kelin_api"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS public.v4_spirit_chat_commit_send("
        "uuid, uuid, text, text, text, boolean, text, text)"
    )
    for table in _TABLES:
        op.execute(f"REVOKE SELECT, INSERT, UPDATE ON TABLE public.{table} FROM {_DEFINER}")
