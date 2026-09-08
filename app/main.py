from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import Settings, get_settings
from app.core.db_probe import DatabaseProbe, build_database_probe
from app.core.logging import configure_logging, get_logger
from app.health import router as health_router

RequestResponseEndpoint = Callable[[Request], Awaitable[Response]]


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


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
            route=route,
            method=request.method,
            status_code=response.status_code,
            latency_ms=int((perf_counter() - started) * 1000),
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
    )
    application.state.settings = resolved
    application.state.db_probe = db_probe or build_database_probe(resolved.database_url_api)
    application.include_router(health_router)
    application.add_middleware(AccessLogMiddleware)
    application.add_middleware(RequestIdMiddleware)
    get_logger(capability="http").info("app_boot", **resolved.config_presence())
    return application


app = create_app()
