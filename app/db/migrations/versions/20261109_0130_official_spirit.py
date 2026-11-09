"""Seed the official ask spirit and remember an agent session.

Revision ID: 20261109_0130
Revises: 20261105_0129
Create Date: 2026-11-09

The spirit is not a public skill and its owner has visits turned off.
The asker can update only the new session column.
"""

from collections.abc import Sequence

from alembic import op
from app.domain.ask.official import (
    OFFICIAL_CLIENT_ID,
    OFFICIAL_INVITE,
    OFFICIAL_NAME,
    OFFICIAL_SPIRIT_ID,
    OFFICIAL_USER_ID,
)
from sqlalchemy import text

revision: str = "20261109_0130"
down_revision: str | None = "20261105_0129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFINER = "kelin_definer"
_TABLE = "public.ask_questions"


def upgrade() -> None:
    op.execute(
        text(
            "INSERT INTO auth.users (id) VALUES (:user_id) ON CONFLICT (id) DO NOTHING"
        ).bindparams(user_id=OFFICIAL_USER_ID)
    )
    op.execute(
        text(
            "INSERT INTO public.spirits ("
            "id, user_id, client_id, name, egg, invite_code, hatched_at"
            ") VALUES ("
            ":spirit_id, :user_id, :client_id, :name, 'warm', :invite, now()"
            ") ON CONFLICT (id) DO NOTHING"
        ).bindparams(
            spirit_id=OFFICIAL_SPIRIT_ID,
            user_id=OFFICIAL_USER_ID,
            client_id=OFFICIAL_CLIENT_ID,
            name=OFFICIAL_NAME,
            invite=OFFICIAL_INVITE,
        )
    )
    op.execute(
        text(
            "INSERT INTO public.user_preferences (user_id, visit_on) "
            "VALUES (:user_id, false) "
            "ON CONFLICT (user_id) DO UPDATE SET visit_on = false"
        ).bindparams(user_id=OFFICIAL_USER_ID)
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public.v3_official_ask_target()
        RETURNS TABLE (spirit_id uuid, owner_user_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
          SELECT id, user_id
          FROM public.spirits
          WHERE id = '{OFFICIAL_SPIRIT_ID}'::uuid
            AND user_id = '{OFFICIAL_USER_ID}'::uuid
        $$
        """
    )
    op.execute(f"ALTER FUNCTION public.v3_official_ask_target() OWNER TO {_DEFINER}")
    op.execute("REVOKE ALL ON FUNCTION public.v3_official_ask_target() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.v3_official_ask_target() TO kelin_api")
    op.execute(f"ALTER TABLE {_TABLE} ADD COLUMN agent_session_id text")
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT ck_ask_questions_agent_session "
        "CHECK (agent_session_id IS NULL OR char_length(agent_session_id) >= 1)"
    )
    op.execute(f"GRANT UPDATE (agent_session_id) ON TABLE {_TABLE} TO kelin_api")


def downgrade() -> None:
    op.execute(f"REVOKE UPDATE (agent_session_id) ON TABLE {_TABLE} FROM kelin_api")
    op.execute(
        f"ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS ck_ask_questions_agent_session"
    )
    op.execute(f"ALTER TABLE {_TABLE} DROP COLUMN IF EXISTS agent_session_id")
    op.execute("DROP FUNCTION IF EXISTS public.v3_official_ask_target()")
