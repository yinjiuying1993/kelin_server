from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.db_probe import DatabaseUnavailable
from app.core.logging import get_logger

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    probe = request.app.state.db_probe
    checks: dict[str, str] = {
        "config": "ok",
        "database": "ok",
        "migration": "not_applicable",
    }
    presence = settings.config_presence()
    get_logger(capability="health").info("health_ready_start", **presence)

    try:
        await probe.ping()
    except DatabaseUnavailable as exc:
        checks["database"] = "unavailable"
        get_logger(capability="health").warning(
            "health_ready_failed",
            error_code="database_unavailable",
            reason=str(exc),
            **presence,
        )
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "checks": checks},
        )

    get_logger(capability="health").info("health_ready_ok", **presence)
    return JSONResponse(status_code=200, content={"status": "ready", "checks": checks})
