from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from app.api.deps import provider_from_app, session_factory_from_app
from app.api.v1.spirit_map import mutation_result_from_create
from app.core.envelope import request_id_of, utc_server_time
from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser, require_current_user
from app.db.session import claimed_read_transaction, claimed_transaction
from app.repositories.spirit_profile import SqlSpiritProfile
from app.schemas.spirit import (
    CreateSpiritRequest,
    SpiritCreateErrorEnvelope,
    SpiritCreateSuccessEnvelope,
)
from app.schemas.spirit_profile import (
    AvatarUploadRequest,
    NicknameSaveRequest,
    SpiritOwnerPage,
    SpiritOwnerPageEnvelope,
    SpiritOwnerPageErrorEnvelope,
)
from app.services.spirit import create_spirit_if_absent
from app.services.spirit_profile import (
    image_safety_input,
    read_owner_page,
    restore_default_avatar,
    save_avatar,
    save_nickname,
)

router = APIRouter(prefix="/api/v1", tags=["spirits"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "model": SpiritCreateErrorEnvelope,
        "description": "UNAUTHENTICATED",
    },
    409: {
        "model": SpiritCreateErrorEnvelope,
        "description": "CONFLICT or IDEMPOTENCY_CONFLICT",
    },
    422: {
        "model": SpiritCreateErrorEnvelope,
        "description": "INVALID_INPUT",
    },
}


@router.post(
    "/spirits",
    response_model=SpiritCreateSuccessEnvelope,
    responses=_ERROR_RESPONSES,
    status_code=status.HTTP_200_OK,
)
async def create_spirit(
    request: Request,
    _body: CreateSpiritRequest,
    _user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SpiritCreateSuccessEnvelope:
    """Contract-only path. Create settlement is P05-T04; HTTP wiring of success is T05."""
    factory = session_factory_from_app(request.app)
    async with claimed_transaction(factory, _user) as session:
        created = await create_spirit_if_absent(session, _user, _body)
    return SpiritCreateSuccessEnvelope(
        data=mutation_result_from_create(created),
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )


_PAGE_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": SpiritOwnerPageErrorEnvelope, "description": "UNAUTHENTICATED"},
    404: {"model": SpiritOwnerPageErrorEnvelope, "description": "NOT_FOUND"},
    422: {
        "model": SpiritOwnerPageErrorEnvelope,
        "description": "INVALID_INPUT or MODERATION_REJECTED",
    },
    503: {"model": SpiritOwnerPageErrorEnvelope, "description": "DEPENDENCY_UNAVAILABLE"},
    504: {"model": SpiritOwnerPageErrorEnvelope, "description": "PROVIDER_TIMEOUT"},
}


@router.get(
    "/spirits/me",
    response_model=SpiritOwnerPageEnvelope,
    responses=_PAGE_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def read_my_spirit(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SpiritOwnerPageEnvelope:
    page = await read_owner_page(
        _Profile(session_factory_from_app(request.app), user),
        user_id=user.id,
        now=datetime.now(UTC),
    )
    return _page_envelope(request, page)


@router.put(
    "/spirits/me/nickname",
    response_model=SpiritOwnerPageEnvelope,
    responses=_PAGE_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def save_my_nickname(
    request: Request,
    body: NicknameSaveRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SpiritOwnerPageEnvelope:
    page = await save_nickname(
        _Profile(session_factory_from_app(request.app), user),
        _TextSafety(provider_from_app(request.app)),
        user_id=user.id,
        nickname=body.nickname,
        now=datetime.now(UTC),
    )
    return _page_envelope(request, page)


@router.post(
    "/spirits/me/avatar",
    response_model=SpiritOwnerPageEnvelope,
    responses=_PAGE_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def save_my_avatar(
    request: Request,
    body: AvatarUploadRequest,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SpiritOwnerPageEnvelope:
    backend = getattr(request.app.state, "object_backend", None)
    if backend is None:
        raise ApiError(
            "DEPENDENCY_UNAVAILABLE",
            public_error_message("DEPENDENCY_UNAVAILABLE"),
            status_code=503,
            retryable=True,
        )
    page = await save_avatar(
        _Profile(session_factory_from_app(request.app), user),
        _ImageSafety(provider_from_app(request.app)),
        _AvatarObjects(backend),
        user_id=user.id,
        encoded=body.content_base64,
        now=datetime.now(UTC),
    )
    return _page_envelope(request, page)


@router.post(
    "/spirits/me/avatar/default",
    response_model=SpiritOwnerPageEnvelope,
    responses=_PAGE_ERRORS,
    status_code=status.HTTP_200_OK,
)
async def restore_my_avatar(
    request: Request,
    user: Annotated[CurrentUser, Depends(require_current_user)],
) -> SpiritOwnerPageEnvelope:
    page = await restore_default_avatar(
        _Profile(session_factory_from_app(request.app), user),
        user_id=user.id,
        now=datetime.now(UTC),
    )
    return _page_envelope(request, page)


class _Profile:
    def __init__(self, factory: Any, user: CurrentUser) -> None:
        self._factory = factory
        self._user = user

    async def load(self, user_id: UUID) -> Any:
        async with claimed_read_transaction(self._factory, self._user) as session:
            return await SqlSpiritProfile(session).load(user_id)

    async def set_nickname(self, user_id: UUID, nickname: str | None) -> bool:
        async with claimed_transaction(self._factory, self._user) as session:
            return await SqlSpiritProfile(session).set_nickname(user_id, nickname)

    async def set_custom_avatar(self, user_id: UUID, avatar_key: str | None) -> bool:
        async with claimed_transaction(self._factory, self._user) as session:
            return await SqlSpiritProfile(session).set_custom_avatar(user_id, avatar_key)


class _TextSafety:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def decision(self, text: str) -> str:
        return await self._provider.moderate_text(text)


class _ImageSafety:
    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def decision(self, body: bytes) -> str:
        result = await self._provider.moderate(image_safety_input(body))
        return str(result.decision)


class _AvatarObjects:
    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def put(self, key: str, body: bytes) -> None:
        self._backend.put("kelin-spirit-avatar", key, body, content_type="image/jpeg")


def _page_envelope(request: Request, page: SpiritOwnerPage) -> SpiritOwnerPageEnvelope:
    return SpiritOwnerPageEnvelope(
        data=page,
        request_id=request_id_of(request),
        server_time=utc_server_time(),
    )
