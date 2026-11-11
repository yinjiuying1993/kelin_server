"""Copy a committed spirit face onto chat participants.

Revision ID: 20261110_0136
Revises: 20261110_0135
Create Date: 2026-11-10

The spirit module does not call this function. kelin_api does, after that
module's transaction has committed. kelin_definer bypasses RLS so one call
can refresh every participant who sees this spirit.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261110_0136"
down_revision: str | None = "20261110_0135"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.v4_spirit_chat_apply_face(
          target uuid,
          face_name text,
          face_avatar text,
          face_available boolean
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE
          changed integer;
        BEGIN
          IF target IS NULL
            OR face_name IS NULL
            OR face_avatar IS NULL
            OR face_available IS NULL
            OR char_length(face_name) < 1
            OR char_length(face_name) > 20
            OR char_length(face_avatar) < 1
            OR char_length(face_avatar) > 300 THEN
            RETURN 0;
          END IF;
          UPDATE public.spirit_chat_participants AS seen
          SET peer_display_name = face_name,
              peer_avatar_url = face_avatar,
              peer_available = face_available
          WHERE seen.peer_spirit_id = target;
          GET DIAGNOSTICS changed = ROW_COUNT;
          INSERT INTO public.spirit_chat_outbox (
            recipient_user_id, thread_id, event_type, seq, event_body
          )
          SELECT
            seen.user_id,
            seen.thread_id,
            'thread.updated',
            seen.last_visible_seq,
            jsonb_build_object(
              'text', COALESCE(latest.body, ''),
              'unread', (
                SELECT count(*)::int
                FROM public.spirit_chat_messages AS msg
                WHERE msg.thread_id = seen.thread_id
                  AND msg.sender_spirit_id <> seen.my_spirit_id
                  AND msg.seq > seen.last_read_seq
                  AND msg.seq > seen.clear_seq
              ),
              'peer_spirit_id', seen.peer_spirit_id,
              'display_name', seen.peer_display_name,
              'avatar_url', seen.peer_avatar_url,
              'available', seen.peer_available,
              'hidden', false
            )::text
          FROM public.spirit_chat_participants AS seen
          LEFT JOIN public.spirit_chat_messages AS latest
            ON latest.thread_id = seen.thread_id
           AND latest.seq = seen.last_visible_seq
          WHERE seen.peer_spirit_id = target
            AND seen.last_visible_seq > seen.clear_seq;
          INSERT INTO public.spirit_chat_outbox (
            recipient_user_id, thread_id, event_type, seq, event_body
          )
          SELECT
            mine.user_id,
            mine.thread_id,
            'thread.updated',
            mine.last_visible_seq,
            jsonb_build_object(
              'text', COALESCE(latest.body, ''),
              'unread', (
                SELECT count(*)::int
                FROM public.spirit_chat_messages AS msg
                WHERE msg.thread_id = mine.thread_id
                  AND msg.sender_spirit_id <> mine.my_spirit_id
                  AND msg.seq > mine.last_read_seq
                  AND msg.seq > mine.clear_seq
              ),
              'peer_spirit_id', mine.peer_spirit_id,
              'display_name', mine.peer_display_name,
              'avatar_url', mine.peer_avatar_url,
              'available', mine.peer_available,
              'hidden', false,
              'own_display_name', face_name,
              'own_avatar_url', face_avatar
            )::text
          FROM public.spirit_chat_participants AS mine
          LEFT JOIN public.spirit_chat_messages AS latest
            ON latest.thread_id = mine.thread_id
           AND latest.seq = mine.last_visible_seq
          WHERE mine.my_spirit_id = target
            AND mine.peer_spirit_id <> target
            AND mine.last_visible_seq > mine.clear_seq;
          RETURN changed;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION public.v4_spirit_chat_apply_face(uuid, text, text, boolean) "
        "OWNER TO kelin_definer"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_spirit_chat_apply_face(uuid, text, text, boolean) "
        "FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.v4_spirit_chat_apply_face(uuid, text, text, boolean) "
        "TO kelin_api"
    )
    op.execute(
        "CREATE INDEX ix_spirit_chat_participants_peer "
        "ON public.spirit_chat_participants (peer_spirit_id)"
    )
    op.execute(
        "CREATE INDEX ix_spirit_chat_participants_mine "
        "ON public.spirit_chat_participants (my_spirit_id)"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS public.v4_spirit_chat_apply_face(uuid, text, text, boolean)"
    )
    op.execute("DROP INDEX IF EXISTS public.ix_spirit_chat_participants_peer")
    op.execute("DROP INDEX IF EXISTS public.ix_spirit_chat_participants_mine")
