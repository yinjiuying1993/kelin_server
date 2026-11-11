"""V4-T01 nickname and avatar stay on the spirit and survive a rejected save."""

from __future__ import annotations

import base64
import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.domain.spirit_profile import avatar_url, default_avatar_key
from app.providers.errors import ProviderError
from app.repositories import spirit as spirit_sql
from app.repositories.spirit_profile import OwnerProfile
from app.services.spirit_profile import (
    read_owner_page,
    restore_default_avatar,
    save_avatar,
    save_nickname,
)

_OWNER = UUID("00000000-0000-4000-8000-0000000000b1")
_SPIRIT = UUID("00000000-0000-4000-8000-0000000000b2")
_NOW = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)
_HATCHED = datetime(2026, 11, 8, 2, 0, tzinfo=UTC)
_JPEG = b"\xff\xd8\xff" + b"avatar"


def _row(**overrides: object) -> OwnerProfile:
    base = OwnerProfile(
        spirit_id=_SPIRIT,
        name="原名",
        nickname=None,
        stage="whelp",
        invite_code="ABCDEFGH",
        hatched_at=_HATCHED,
        default_avatar_key=default_avatar_key(_OWNER),
        custom_avatar_key=None,
        help_count=2,
        skills=(),
    )
    values = {**base.__dict__, **overrides}
    return OwnerProfile(**values)


class _Store:
    def __init__(self, row: OwnerProfile | None) -> None:
        self.row = row
        self.nicknames: list[str | None] = []
        self.avatars: list[str | None] = []

    async def load(self, user_id: UUID) -> OwnerProfile | None:
        del user_id
        return self.row

    async def set_nickname(self, user_id: UUID, nickname: str | None) -> bool:
        del user_id
        if self.row is None:
            return False
        self.nicknames.append(nickname)
        self.row = _row(
            nickname=nickname,
            custom_avatar_key=self.row.custom_avatar_key,
            help_count=self.row.help_count,
        )
        return True

    async def set_custom_avatar(self, user_id: UUID, avatar_key: str | None) -> bool:
        del user_id
        if self.row is None:
            return False
        self.avatars.append(avatar_key)
        self.row = _row(
            nickname=self.row.nickname,
            custom_avatar_key=avatar_key,
            help_count=self.row.help_count,
        )
        return True


class _Safety:
    def __init__(self, decision: str | Exception = "allow") -> None:
        self.decision_value = decision
        self.calls = 0

    async def decision(self, payload: object) -> str:
        del payload
        self.calls += 1
        if isinstance(self.decision_value, Exception):
            raise self.decision_value
        return self.decision_value


class _Objects:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def put(self, key: str, body: bytes) -> None:
        del body
        self.keys.append(key)


def test_default_avatar_is_stable_for_the_same_owner() -> None:
    first = default_avatar_key(_OWNER)
    assert first == default_avatar_key(_OWNER)
    assert avatar_url(first) == avatar_url(first)
    assert "default_avatar_key" in inspect.getsource(spirit_sql.insert_spirit)


@pytest.mark.asyncio
async def test_unset_nickname_shows_the_original_name_and_default_avatar() -> None:
    page = await read_owner_page(_Store(_row()), user_id=_OWNER, now=_NOW)
    assert page.name == "原名"
    assert page.nickname is None
    assert page.avatar_is_default is True
    assert page.avatar_url == avatar_url(default_avatar_key(_OWNER))
    assert page.companion_days == 2
    assert page.help_count == 2
    assert page.invite_code == "ABCDEFGH"


@pytest.mark.asyncio
async def test_blank_nickname_clears_without_safety() -> None:
    store = _Store(_row(nickname="小名"))
    safety = _Safety()
    page = await save_nickname(store, safety, user_id=_OWNER, nickname="   ", now=_NOW)
    assert page.nickname is None
    assert safety.calls == 0
    assert store.nicknames == [None]


@pytest.mark.asyncio
async def test_long_blocked_and_failed_nickname_keep_the_previous_one() -> None:
    store = _Store(_row(nickname="小名"))
    with pytest.raises(ApiError) as too_long:
        await save_nickname(
            store, _Safety(), user_id=_OWNER, nickname="1234567890123", now=_NOW
        )
    assert too_long.value.code == "INVALID_INPUT"
    blocked = _Safety("block")
    with pytest.raises(ApiError) as rejected:
        await save_nickname(store, blocked, user_id=_OWNER, nickname="新名", now=_NOW)
    assert rejected.value.code == "MODERATION_REJECTED"
    failed = _Safety(ProviderError("PROVIDER_TIMEOUT"))
    with pytest.raises(ApiError) as timed:
        await save_nickname(store, failed, user_id=_OWNER, nickname="新名", now=_NOW)
    assert timed.value.code == "PROVIDER_TIMEOUT"
    assert timed.value.retryable is True
    assert store.nicknames == []
    assert store.row is not None
    assert store.row.nickname == "小名"


@pytest.mark.asyncio
async def test_rejected_avatar_does_not_replace_the_stored_one() -> None:
    store = _Store(_row())
    objects = _Objects()
    encoded = base64.b64encode(_JPEG).decode("ascii")
    with pytest.raises(ApiError) as invalid:
        await save_avatar(
            store, _Safety(), objects, user_id=_OWNER, encoded="not-jpeg", now=_NOW
        )
    assert invalid.value.code == "INVALID_INPUT"
    blocked = _Safety("block")
    with pytest.raises(ApiError) as rejected:
        await save_avatar(
            store, blocked, objects, user_id=_OWNER, encoded=encoded, now=_NOW
        )
    assert rejected.value.code == "MODERATION_REJECTED"
    assert objects.keys == []
    assert store.avatars == []
    page = await save_avatar(
        store, _Safety("allow"), objects, user_id=_OWNER, encoded=encoded, now=_NOW
    )
    assert page.avatar_is_default is False
    assert objects.keys == [f"spirit-avatar/{_SPIRIT}/custom"]
    restored = await restore_default_avatar(store, user_id=_OWNER, now=_NOW)
    assert restored.avatar_is_default is True
    assert restored.avatar_url == avatar_url(default_avatar_key(_OWNER))
