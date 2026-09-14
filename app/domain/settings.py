"""PATCH /spirit field-level patch helpers. Spec §§5.5, 9.5."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import time
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas.settings import PatchSpiritRequest

SPIRIT_PATCH_OPERATION = "spirit.patch"
HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class InvalidPreference(ValueError):
    """A preference value failed the locked settings contract."""


def format_hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def parse_hhmm(value: str) -> time:
    if not HHMM_RE.match(value):
        raise InvalidPreference("dnd must be HH:MM")
    hour, minute = value.split(":", 1)
    return time(hour=int(hour), minute=int(minute))


def require_iana_timezone(value: str) -> str:
    if not value or len(value) > 64:
        raise InvalidPreference("timezone must be IANA")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidPreference("timezone must be IANA") from exc
    return value


def patch_spirit_request_hash(request: PatchSpiritRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"client_id"}, exclude_unset=True)
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def owner_hash(*, user_id: UUID, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        str(user_id).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
