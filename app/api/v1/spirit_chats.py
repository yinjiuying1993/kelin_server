"""Read-only spirit chat HTTP. Sending stays on the WebSocket."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, WebSocket, status
from sqlalchemy.exc import DBAPIError

from app.api.deps import provider_from_app, session_factory_from_app
from app.core.config import Settings
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, JwtAuthError, JwtVerifier, require_current_user
from app.db.session import claimed_read_transaction
from app.repositories.spirit_chat import SqlSpiritChat
from app.repositories.spirit_directory import SqlSpiritDirectory
from app.schemas.spirit_chat import (
    SpiritChatErrorEnvelope,
    SpiritChatListEnvelope,
    SpiritChatMessagePageEnvelope,
    SpiritChatWindowEnvelope,
)
from app.services.spirit_chat.gateway import AUTH_CLOSE, RateGate, serve_connection
from app.services.spirit_chat.hub import Hub
from app.services.spirit_chat.query import list_chat_messages, list_chats, open_by_spirit
from app.services.spirit_chat.runtime import RuntimeCommands, deliver_available
from app.services.spirit_directory import lookup_directory

router = APIRouter(prefix="/api/v1", tags=["spirit-chats"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SpiritChatErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": SpiritChatErrorEnvelope, "description": "NOT_FOUND"},
    422: {"model": SpiritChatErrorEnvelope, "description": "INVALID_CURSOR"},
    503: {"model": SpiritChatErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
}


class _Cards:
    def __init__(self, directory: SqlSpiritDirectory) -> None:
        self._directory = directory

    async def lookup(self, viewer_id: UUID, spirit_id: UUID):
        return await lookup_directory(self._directory, viewer_id=viewer_id, spirit_id=spirit_id)


def _unavailable() -> ApiError:
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


def _secret(request: Request) -> bytes:
    settings = request.app.state.settings
    if not isinstance(settings, Settings):
        raise _unavailable()
    return settings.cursor_signing_key()


@router.get(
    "/spirit-chats",
    response_model=SpiritChatListEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def get_spirit_chats(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
    cursor: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> SpiritChatListEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_read_transaction(factory, user) as session:
            page = await list_chats(
                SqlSpiritChat(session),
                user_id=user.id,
                cursor=cursor,
                limit=limit,
                secret=_secret(request),
                now=datetime.now(UTC),
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return SpiritChatListEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/spirit-chats/by-spirit/{spirit_id}",
    response_model=SpiritChatWindowEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def get_spirit_chat_by_spirit(
    request: Request,
    spirit_id: UUID,
    user: Annotated[CurrentUser, Depends(require_current_user)],
    cursor: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> SpiritChatWindowEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_read_transaction(factory, user) as session:
            window = await open_by_spirit(
                SqlSpiritChat(session),
                _Cards(SqlSpiritDirectory(session)),
                user_id=user.id,
                spirit_id=spirit_id,
                cursor=cursor,
                limit=limit,
                secret=_secret(request),
                now=datetime.now(UTC),
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return SpiritChatWindowEnvelope(
        data=window,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.get(
    "/spirit-chats/{thread_id}/messages",
    response_model=SpiritChatMessagePageEnvelope,
    responses=_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def get_spirit_chat_messages(
    request: Request,
    thread_id: UUID,
    user: Annotated[CurrentUser, Depends(require_current_user)],
    cursor: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> SpiritChatMessagePageEnvelope:
    factory = session_factory_from_app(request.app)
    try:
        async with claimed_read_transaction(factory, user) as session:
            page = await list_chat_messages(
                SqlSpiritChat(session),
                user_id=user.id,
                thread_id=thread_id,
                cursor=cursor,
                limit=limit,
                secret=_secret(request),
                now=datetime.now(UTC),
            )
    except ApiError:
        raise
    except (DBAPIError, OSError, TimeoutError) as exc:
        raise _unavailable() from exc
    return SpiritChatMessagePageEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


@router.websocket("/spirit-chats/stream")
async def spirit_chat_stream(websocket: WebSocket) -> None:
    user = await _socket_user(websocket)
    if user is None:
        await websocket.close(AUTH_CLOSE)
        return
    application = websocket.app
    factory = session_factory_from_app(application)
    hub = _hub(application)
    gate = _gate(application)

    async def receive_timeout(seconds: float) -> str:
        return await asyncio.wait_for(websocket.receive_text(), seconds)

    async def deliver() -> None:
        await deliver_available(factory, hub)

    await serve_connection(
        websocket,
        user_id=user.id,
        expires_at=user.expires_at,
        commands=RuntimeCommands(factory, provider_from_app(application)),
        hub=hub,
        gate=gate,
        deliver=deliver,
        now=lambda: datetime.now(UTC),
        receive_timeout=receive_timeout,
    )


def _hub(application) -> Hub:
    hub = getattr(application.state, "spirit_chat_hub", None)
    if isinstance(hub, Hub):
        return hub
    hub = Hub()
    application.state.spirit_chat_hub = hub
    return hub


def _gate(application) -> RateGate:
    gate = getattr(application.state, "spirit_chat_gate", None)
    if isinstance(gate, RateGate):
        return gate
    gate = RateGate()
    application.state.spirit_chat_gate = gate
    return gate


def _bearer(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        return token or None
    token = websocket.query_params.get("access_token")
    return token or None


async def _socket_user(websocket: WebSocket):
    verifier = getattr(websocket.app.state, "jwt_verifier", None)
    token = _bearer(websocket)
    if token is None or not isinstance(verifier, JwtVerifier):
        return None
    try:
        return await verifier.authenticate(token)
    except JwtAuthError:
        return None


for _route in router.routes:
    if getattr(_route, "path", "").endswith("/spirit-chats/stream"):
        _route.include_in_schema = False
