"""HTTP session wiring. Does not contain create settlement rules."""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ApiError, public_error_message
from app.db.session import create_runtime_engine, create_session_factory
from app.integrations.oss import AliyunOssBackend, backend_from_settings
from app.integrations.storage import PrivateSightStorage, PrivateTtsStorage, PrivateVisitCardStorage
from app.providers.factory import build_provider


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


def provider_from_app(app: FastAPI) -> object:
    existing = getattr(app.state, "provider", None)
    if existing is not None:
        return existing
    settings = getattr(app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise _unavailable()
    return build_provider(settings)


def session_factory_from_app(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    existing = getattr(app.state, "session_factory", None)
    if isinstance(existing, async_sessionmaker):
        return existing
    settings = getattr(app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise _unavailable()
    try:
        url = settings.sqlalchemy_async_url()
    except ValueError as exc:
        raise _unavailable() from exc
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = factory
    return factory


def sight_storage_from_app(app: FastAPI) -> PrivateSightStorage:
    existing = getattr(app.state, "sight_storage", None)
    if isinstance(existing, PrivateSightStorage):
        return existing
    storage = PrivateSightStorage(_object_backend(app))
    app.state.sight_storage = storage
    return storage


def visit_card_storage_from_app(app: FastAPI) -> PrivateVisitCardStorage:
    existing = getattr(app.state, "visit_card_storage", None)
    if isinstance(existing, PrivateVisitCardStorage):
        return existing
    storage = PrivateVisitCardStorage(_object_backend(app))
    app.state.visit_card_storage = storage
    return storage


def tts_storage_from_app(app: FastAPI) -> PrivateTtsStorage:
    existing = getattr(app.state, "tts_storage", None)
    if isinstance(existing, PrivateTtsStorage):
        return existing
    storage = PrivateTtsStorage(_object_backend(app))
    app.state.tts_storage = storage
    return storage


def _object_backend(app: FastAPI) -> AliyunOssBackend | None:
    settings = getattr(app.state, "settings", None)
    if isinstance(settings, Settings):
        return backend_from_settings(settings)
    return None
