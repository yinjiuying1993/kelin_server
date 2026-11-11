"""Claim pending spirit-chat outbox rows for the API process.

Revision ID: 20261110_0135
Revises: 20261110_0134
Create Date: 2026-11-10

The rating worker does not read this table. kelin_api calls the function;
kelin_definer bypasses RLS so one process can finish every recipient's row.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261110_0135"
down_revision: str | None = "20261110_0134"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.v4_spirit_chat_take_outbox(batch integer)
        RETURNS TABLE (
          id uuid,
          recipient_user_id uuid,
          thread_id uuid,
          event_type text,
          seq integer,
          event_body text
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
          IF batch IS NULL OR batch < 1 THEN
            batch := 50;
          END IF;
          RETURN QUERY
          WITH pending AS (
            SELECT box.id
            FROM public.spirit_chat_outbox AS box
            WHERE box.processed_at IS NULL
            ORDER BY box.created_at
            FOR UPDATE SKIP LOCKED
            LIMIT batch
          )
          UPDATE public.spirit_chat_outbox AS taken
          SET processed_at = now()
          FROM pending
          WHERE taken.id = pending.id
          RETURNING
            taken.id,
            taken.recipient_user_id,
            taken.thread_id,
            taken.event_type,
            taken.seq,
            taken.event_body;
        END;
        $$
        """
    )
    op.execute(
        "ALTER FUNCTION public.v4_spirit_chat_take_outbox(integer) OWNER TO kelin_definer"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.v4_spirit_chat_take_outbox(integer) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.v4_spirit_chat_take_outbox(integer) TO kelin_api"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.v4_spirit_chat_take_outbox(integer)")
