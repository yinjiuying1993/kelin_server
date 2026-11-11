"""Owner spirit page. Safety runs outside the write transaction."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.domain.spirit_face import SpiritFace, publish_spirit_face
from app.domain.spirit_profile import (
    InvalidAvatar,
    NicknameTooLong,
    avatar_url,
    cleaned_nickname,
    companion_days,
    custom_avatar_key,
    decode_avatar,
    display_name,
)
from app.providers.errors import ProviderError
from app.providers.types import SafetyInput
from app.repositories.spirit_profile import OwnerProfile
from app.schemas.spirit_profile import SpiritOwnerPage, SpiritPageSkill


class ProfileStore(Protocol):
    async def load(self, user_id: UUID) -> OwnerProfile | None: ...

    async def set_nickname(self, user_id: UUID, nickname: str | None) -> bool: ...

    async def set_custom_avatar(self, user_id: UUID, avatar_key: str | None) -> bool: ...


class TextSafety(Protocol):
    async def decision(self, text: str) -> str: ...


class ImageSafety(Protocol):
    async def decision(self, body: bytes) -> str: ...


class AvatarObjects(Protocol):
    def put(self, key: str, body: bytes) -> None: ...


def page_from(row: OwnerProfile, *, now: datetime) -> SpiritOwnerPage:
    key = row.custom_avatar_key or row.default_avatar_key
    return SpiritOwnerPage(
        id=row.spirit_id,
        name=row.name,
        nickname=row.nickname,
        avatar_url=avatar_url(key),
        avatar_is_default=row.custom_avatar_key is None,
        stage=row.stage,  # type: ignore[arg-type]
        companion_days=companion_days(row.hatched_at, now),
        invite_code=row.invite_code,
        help_count=row.help_count,
        skills=[SpiritPageSkill(title=item.title, rank_name=item.rank_name) for item in row.skills],
    )


async def read_owner_page(
    store: ProfileStore, *, user_id: UUID, now: datetime
) -> SpiritOwnerPage:
    row = await store.load(user_id)
    if row is None:
        raise _missing()
    return page_from(row, now=now)


async def save_nickname(
    store: ProfileStore,
    safety: TextSafety,
    *,
    user_id: UUID,
    nickname: str,
    now: datetime,
) -> SpiritOwnerPage:
    try:
        cleaned = cleaned_nickname(nickname)
    except NicknameTooLong:
        raise _invalid() from None
    if await store.load(user_id) is None:
        raise _missing()
    if cleaned is not None:
        _require_allow(await _text_decision(safety, cleaned))
    if not await store.set_nickname(user_id, cleaned):
        raise _missing()
    return await _publish_page(store, user_id=user_id, now=now)


async def save_avatar(
    store: ProfileStore,
    safety: ImageSafety,
    objects: AvatarObjects,
    *,
    user_id: UUID,
    encoded: str,
    now: datetime,
) -> SpiritOwnerPage:
    try:
        body = decode_avatar(encoded)
    except InvalidAvatar:
        raise _invalid() from None
    current = await store.load(user_id)
    if current is None:
        raise _missing()
    _require_allow(await _image_decision(safety, body))
    key = custom_avatar_key(current.spirit_id)
    objects.put(key, body)
    if not await store.set_custom_avatar(user_id, key):
        raise _missing()
    return await _publish_page(store, user_id=user_id, now=now)


async def restore_default_avatar(
    store: ProfileStore, *, user_id: UUID, now: datetime
) -> SpiritOwnerPage:
    if not await store.set_custom_avatar(user_id, None):
        raise _missing()
    return await _publish_page(store, user_id=user_id, now=now)


async def _publish_page(store: ProfileStore, *, user_id: UUID, now: datetime) -> SpiritOwnerPage:
    page = await read_owner_page(store, user_id=user_id, now=now)
    await publish_spirit_face(
        SpiritFace(
            spirit_id=page.id,
            display_name=display_name(page.name, page.nickname),
            avatar_url=page.avatar_url,
            available=True,
        )
    )
    return page


async def _text_decision(safety: TextSafety, text: str) -> str:
    try:
        return await safety.decision(text)
    except ProviderError as exc:
        raise _provider(exc) from exc


async def _image_decision(safety: ImageSafety, body: bytes) -> str:
    try:
        return await safety.decision(body)
    except ProviderError as exc:
        raise _provider(exc) from exc


def _require_allow(decision: str) -> None:
    if decision == "block":
        raise ApiError(
            "MODERATION_REJECTED",
            public_error_message("MODERATION_REJECTED"),
            status_code=422,
        )
    if decision != "allow":
        raise ApiError(
            "DEPENDENCY_UNAVAILABLE",
            public_error_message("DEPENDENCY_UNAVAILABLE"),
            status_code=503,
            retryable=True,
        )


def image_safety_input(body: bytes) -> SafetyInput:
    return SafetyInput(
        sha256=hashlib.sha256(body).hexdigest(),
        size_bytes=len(body),
        body=body,
    )


def _provider(exc: ProviderError) -> ApiError:
    if exc.code == "PROVIDER_TIMEOUT":
        return ApiError(
            "PROVIDER_TIMEOUT",
            public_error_message("PROVIDER_TIMEOUT"),
            status_code=504,
            retryable=True,
        )
    return ApiError(
        "DEPENDENCY_UNAVAILABLE",
        public_error_message("DEPENDENCY_UNAVAILABLE"),
        status_code=503,
        retryable=True,
    )


def _missing() -> ApiError:
    return ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)


def _invalid() -> ApiError:
    return ApiError("INVALID_INPUT", public_error_message("INVALID_INPUT"), status_code=422)
