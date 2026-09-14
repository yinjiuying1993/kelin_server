from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.v1.account import router as account_router
from app.api.v1.bootstrap import router as bootstrap_router
from app.api.v1.chat import router as chat_router
from app.api.v1.devices import router as devices_router
from app.api.v1.extract import router as extract_router
from app.api.v1.feeds import router as feeds_router
from app.api.v1.memories import router as memories_router
from app.api.v1.messages import router as messages_router
from app.api.v1.moderate import router as moderate_router
from app.api.v1.onboarding import router as onboarding_router
from app.api.v1.pacts import router as pacts_router
from app.api.v1.recall import router as recall_router
from app.api.v1.report import router as report_router
from app.api.v1.settings import router as settings_router
from app.api.v1.social import router as social_router
from app.api.v1.speech import playback_router
from app.api.v1.speech import router as speech_router
from app.api.v1.spirits import router as spirits_router
from app.api.v1.storage import router as storage_router
from app.core.config import Settings, get_settings
from app.core.db_probe import DatabaseProbe, build_database_probe
from app.core.envelope import (
    EnvelopeSuccessMiddleware,
    RequestIdMiddleware,
    register_envelope_handlers,
)
from app.core.logging import configure_logging, get_logger
from app.core.security import HttpxJwksFetcher, JwtVerifier
from app.domain.debug import debug_router_enabled
from app.health import router as health_router
from app.integrations.storage import PrivateSightStorage, PrivateTtsStorage

RequestResponseEndpoint = Callable[[Request], Awaitable[Response]]


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    yield
    engine = getattr(application.state, "engine", None)
    if engine is not None:
        await engine.dispose()


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = perf_counter()
        response = await call_next(request)
        route = request.scope.get("path", "")
        request_id = getattr(request.state, "request_id", None)
        logger = get_logger(capability="http")
        logger.info(
            "http_access",
            request_id=str(request_id) if request_id else None,
            user_id_hash=getattr(request.state, "user_id_hash", None),
            route=route,
            method=request.method,
            status_code=response.status_code,
            latency_ms=int((perf_counter() - started) * 1000),
            error_code=getattr(request.state, "error_code", None),
        )
        return response


def create_app(
    settings: Settings | None = None,
    db_probe: DatabaseProbe | None = None,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved)
    docs_url = "/docs" if resolved.docs_enabled else None
    application = FastAPI(
        title="kelin-server",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url="/redoc" if resolved.docs_enabled else None,
        openapi_url="/openapi.json" if resolved.docs_enabled else None,
        lifespan=_lifespan,
    )
    application.state.settings = resolved
    application.state.db_probe = db_probe or build_database_probe(resolved.database_url_api)
    application.state.jwt_verifier = _build_jwt_verifier(resolved)
    application.state.engine = None
    application.state.session_factory = None
    application.state.sight_storage = PrivateSightStorage()
    application.state.tts_storage = PrivateTtsStorage()
    application.include_router(health_router)
    application.include_router(bootstrap_router)
    application.include_router(chat_router)
    application.include_router(extract_router)
    application.include_router(feeds_router)
    application.include_router(memories_router)
    application.include_router(messages_router)
    application.include_router(moderate_router)
    application.include_router(onboarding_router)
    application.include_router(recall_router)
    application.include_router(pacts_router)
    application.include_router(report_router)
    application.include_router(settings_router)
    application.include_router(account_router)
    application.include_router(devices_router)
    application.include_router(social_router)
    application.include_router(speech_router)
    application.include_router(playback_router)
    application.include_router(spirits_router)
    application.include_router(storage_router)
    if debug_router_enabled(resolved):
        from app.api.v1.debug import router as debug_router

        application.include_router(debug_router)
    register_envelope_handlers(application)
    application.add_middleware(AccessLogMiddleware)
    application.add_middleware(EnvelopeSuccessMiddleware)
    application.add_middleware(RequestIdMiddleware)
    get_logger(capability="http").info("app_boot", **resolved.config_presence())
    return application


def _build_jwt_verifier(settings: Settings) -> JwtVerifier | None:
    issuer = settings.supabase_jwt_issuer
    audience = settings.supabase_jwt_audience
    jwks_url = settings.supabase_jwks_url
    if issuer is None or audience is None or jwks_url is None:
        return None
    return JwtVerifier(
        issuer=issuer,
        audience=audience,
        fetcher=HttpxJwksFetcher(jwks_url),
    )


app = create_app()
