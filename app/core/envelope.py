"""HTTP Envelope helpers. Spec §§4.1 and 5.1. Never put stack traces or SQL in bodies."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.errors import (
    ApiError,
    public_error_code,
    public_error_message,
)
from app.core.logging import get_logger
from app.schemas.envelope import Envelope, EnvelopeError

RequestResponseEndpoint = Callable[[Request], Awaitable[Response]]


def resolve_request_id(header: str | None) -> str:
    if header:
        try:
            return str(UUID(header))
        except ValueError:
            pass
    return str(uuid4())


def request_id_of(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    resolved = resolve_request_id(request.headers.get("X-Request-ID"))
    request.state.request_id = resolved
    return resolved


def utc_server_time() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def success_envelope(request: Request, data: Any) -> dict[str, Any]:
    return Envelope(
        ok=True,
        data=data,
        error=None,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    ).model_dump(mode="json")


def error_envelope(
    request: Request,
    *,
    code: str,
    message: str,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return Envelope(
        ok=False,
        data=None,
        error=EnvelopeError(code=code, message=message, retryable=retryable, details=details),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    ).model_dump(mode="json")


def envelope_response(
    request: Request,
    *,
    status_code: int,
    payload: dict[str, Any],
) -> JSONResponse:
    request_id = str(payload["request_id"])
    return JSONResponse(
        status_code=status_code,
        content=payload,
        headers={"X-Request-ID": request_id},
    )


def _already_envelope(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("ok"), bool)
        and "request_id" in payload
        and "server_time" in payload
        and "error" in payload
    )


async def _response_body(response: Response) -> bytes:
    iterator = getattr(response, "body_iterator", None)
    if iterator is None:
        raw = getattr(response, "body", b"")
        return raw if isinstance(raw, bytes) else b""
    chunks: list[bytes] = []
    async for chunk in iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else bytes(chunk))
    return b"".join(chunks)


class EnvelopeSuccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        path = request.url.path
        if not path.startswith("/api/v1") or response.status_code >= 400:
            return response
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response
        body = await _response_body(response)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        if _already_envelope(payload):
            wrapped = payload
        else:
            wrapped = success_envelope(request, payload)
        return envelope_response(request, status_code=response.status_code, payload=wrapped)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = resolve_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _validation_details(exc: RequestValidationError) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    for item in exc.errors():
        loc = item.get("loc", ())
        fields.append(
            {
                "loc": [str(part) for part in loc],
                "type": str(item.get("type", "value_error")),
            }
        )
    return {"fields": fields}


def _remember_error_code(request: Request, code: str) -> None:
    request.state.error_code = code


def register_envelope_handlers(application: FastAPI) -> None:
    @application.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        _remember_error_code(request, exc.code)
        return envelope_response(
            request,
            status_code=exc.status_code,
            payload=error_envelope(
                request,
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
                details=exc.details,
            ),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        _remember_error_code(request, "INVALID_INPUT")
        return envelope_response(
            request,
            status_code=422,
            payload=error_envelope(
                request,
                code="INVALID_INPUT",
                message=public_error_message("INVALID_INPUT"),
                details=_validation_details(exc),
            ),
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = public_error_code(exc.status_code, exc.detail)
        _remember_error_code(request, code)
        return envelope_response(
            request,
            status_code=exc.status_code,
            payload=error_envelope(
                request,
                code=code,
                message=public_error_message(code),
            ),
        )

    @application.exception_handler(Exception)
    async def unhandled_handler(request: Request, _exc: Exception) -> JSONResponse:
        _remember_error_code(request, "INTERNAL_ERROR")
        get_logger(capability="http").error(
            "unhandled_error",
            error_code="INTERNAL_ERROR",
            request_id=request_id_of(request),
        )
        return envelope_response(
            request,
            status_code=500,
            payload=error_envelope(
                request,
                code="INTERNAL_ERROR",
                message=public_error_message("INTERNAL_ERROR"),
            ),
        )
