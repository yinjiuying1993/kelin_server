"""Transaction-local RLS claims. Spec §4.3: set_config third argument must be true."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.errors import ApiError, public_error_message
from app.db.current_user import CurrentUser
from app.db.roles import RUNTIME_ROLES

CLAIM_SUB = "request.jwt.claim.sub"
CLAIM_ROLE = "request.jwt.claim.role"
DEFAULT_DB_ROLE = "kelin_api"

# is_local=true: cleared on commit/rollback. Session-level (false) is a stop condition.
INJECT_CLAIM_SQL = """
SELECT
  set_config('request.jwt.claim.sub', :sub, true),
  set_config('request.jwt.claim.role', :role, true)
"""

# Must be the first statement after BEGIN. Spec §§9.2, 15.2.
READ_SNAPSHOT_SQL = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"


# Login-role insert before SET LOCAL ROLE. Local auth.users is a stand-in, not Supabase Auth.
ENSURE_AUTH_USER_SQL = "INSERT INTO auth.users (id) VALUES (:id) ON CONFLICT (id) DO NOTHING"


class ClaimBind(Protocol):
    async def execute(self, statement: Any, parameters: Any = None) -> Any: ...


def _require_runtime_role(db_role: str) -> str:
    if db_role not in RUNTIME_ROLES:
        raise ValueError("unsupported database role")
    return db_role


def _install_checkout_reset(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "checkout")
    def _reset_on_checkout(
        dbapi_connection: Any,
        connection_record: Any,
        connection_proxy: Any,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("RESET ALL")
        finally:
            cursor.close()


def create_runtime_engine(
    url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 10,
) -> AsyncEngine:
    engine = create_async_engine(url, pool_size=pool_size, max_overflow=max_overflow)
    _install_checkout_reset(engine)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def _ensure_local_auth_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    if get_settings().app_env == "prod":
        return
    await session.execute(text(ENSURE_AUTH_USER_SQL), {"id": user_id})


async def inject_transaction_claim(bind: ClaimBind, user: CurrentUser) -> None:
    await bind.execute(
        text(INJECT_CLAIM_SQL),
        {"sub": str(user.id), "role": user.auth_role},
    )


async def read_claim_sub(bind: ClaimBind) -> str | None:
    result = await bind.execute(text("SELECT current_setting('request.jwt.claim.sub', true)"))
    value = result.scalar_one()
    if value is None or value == "":
        return None
    return str(value)


async def read_claim_role(bind: ClaimBind) -> str | None:
    result = await bind.execute(text("SELECT current_setting('request.jwt.claim.role', true)"))
    value = result.scalar_one()
    if value is None or value == "":
        return None
    return str(value)


@asynccontextmanager
async def claimed_transaction(
    session_factory: async_sessionmaker[AsyncSession],
    user: CurrentUser | None,
    *,
    db_role: str = DEFAULT_DB_ROLE,
    allow_account_deleting: bool = False,
) -> AsyncIterator[AsyncSession]:
    role = _require_runtime_role(db_role)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(text("RESET ALL"))
            if user is not None:
                await _ensure_local_auth_user(session, user.id)
            await session.execute(text(f"SET LOCAL ROLE {role}"))
            if user is not None:
                await inject_transaction_claim(session, user)
                if role == DEFAULT_DB_ROLE and not allow_account_deleting:
                    await _reject_if_account_deleting(session)
            yield session


@asynccontextmanager
async def claimed_read_transaction(
    session_factory: async_sessionmaker[AsyncSession],
    user: CurrentUser | None,
    *,
    db_role: str = DEFAULT_DB_ROLE,
) -> AsyncIterator[AsyncSession]:
    """Owner-claimed REPEATABLE READ snapshot. Must not write."""

    role = _require_runtime_role(db_role)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(text(READ_SNAPSHOT_SQL))
            await session.execute(text(f"SET LOCAL ROLE {role}"))
            if user is not None:
                await inject_transaction_claim(session, user)
                if role == DEFAULT_DB_ROLE:
                    await _reject_if_account_deleting(session)
            yield session


async def _reject_if_account_deleting(session: AsyncSession) -> None:
    pending = await session.scalar(text("SELECT private.account_is_deleting()"))
    if pending:
        raise ApiError(
            "ACCOUNT_DELETE_PENDING",
            public_error_message("ACCOUNT_DELETE_PENDING"),
            status_code=409,
        )
