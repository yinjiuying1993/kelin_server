import asyncio

from alembic import context
from app.core.config import get_settings
from app.db.base import Base
from app.db.identity import assert_disposable_kelin_test_database
from app.models import (  # noqa: F401
    AccountConsent,
    AccountDeletion,
    ActionEvent,
    ActionQuota,
    AiUsage,
    AskQuestion,
    AskRating,
    AskTurn,
    AuthUser,
    ChatOverlay,
    ConversationWindow,
    CraftLedger,
    CraftMatchIndex,
    CraftSelfTestTicket,
    CraftSettlement,
    CraftSkill,
    DailyUsage,
    Device,
    Friend,
    IdempotencyRecord,
    Memory,
    Message,
    NotificationDelivery,
    NpcProfile,
    OutboxEvent,
    Pact,
    PactMistake,
    PactSession,
    Postcard,
    Report,
    RoomProjection,
    SightUpload,
    Spirit,
    SpiritAwareness,
    StyleSample,
    UserPreference,
    Visit,
    VisitCard,
)
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

target_metadata = Base.metadata


def _sqlalchemy_url() -> str:
    get_settings.cache_clear()
    settings = get_settings()
    url = settings.sqlalchemy_async_url()
    if settings.app_env == "test":
        assert_disposable_kelin_test_database(url, settings.app_env)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_sqlalchemy_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section) or {}
    configuration = dict(section)
    configuration["sqlalchemy.url"] = _sqlalchemy_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
