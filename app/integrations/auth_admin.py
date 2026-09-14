"""Supabase Auth Admin adapter. Spec §14.9.

Worker never DELETEs auth.users as kelin_worker. The SQL DELETE runs in a
BYPASSRLS definer invoked by the login role.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


class AuthAdminError(Exception):
    """Auth user deletion failed and must be retried."""


class InProcessAuthAdmin:
    def __init__(self, url: str) -> None:
        self._url = url

    async def delete_user(self, user_id: UUID) -> None:
        engine = create_async_engine(self._url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("RESET ALL"))
                await conn.execute(
                    text("SELECT public.delete_auth_user(:id)"),
                    {"id": user_id},
                )
        except Exception as exc:
            raise AuthAdminError("auth delete failed") from exc
        finally:
            await engine.dispose()
