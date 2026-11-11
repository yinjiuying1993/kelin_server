"""Owner spirit page rules. Nickname and avatar stay on the spirit, not in chat."""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from uuid import UUID

_DEFAULT_KEYS = (
    "spirit-avatar/default-1",
    "spirit-avatar/default-2",
    "spirit-avatar/default-3",
    "spirit-avatar/default-4",
)
_NICKNAME_LIMIT = 12
_AVATAR_MAX_BYTES = 5 * 1024 * 1024
_JPEG_MAGIC = b"\xff\xd8\xff"


class NicknameTooLong(ValueError):
    """More than 12 characters after trimming. The previous nickname stays."""


class InvalidAvatar(ValueError):
    """Not a static JPEG, or outside the size limit. The previous avatar stays."""


def default_avatar_key(owner_id: UUID) -> str:
    return _DEFAULT_KEYS[owner_id.int % len(_DEFAULT_KEYS)]


def avatar_url(key: str) -> str:
    return f"https://kelin.invalid/{key}"


def display_name(name: str, nickname: str | None) -> str:
    if nickname is None:
        return name
    text = nickname.strip()
    return text or name


def current_avatar_key(default_key: str, custom_key: str | None) -> str:
    return custom_key or default_key


def cleaned_nickname(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if len(text) > _NICKNAME_LIMIT:
        raise NicknameTooLong
    return text


def companion_days(hatched_at: datetime | None, now: datetime) -> int:
    if hatched_at is None:
        return 0
    start = _utc(hatched_at).date()
    end = _utc(now).date()
    return max(0, (end - start).days)


def decode_avatar(encoded: str) -> bytes:
    try:
        body = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidAvatar from exc
    if not body or len(body) > _AVATAR_MAX_BYTES or not body.startswith(_JPEG_MAGIC):
        raise InvalidAvatar
    return body


def custom_avatar_key(spirit_id: UUID) -> str:
    return f"spirit-avatar/{spirit_id}/custom"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
