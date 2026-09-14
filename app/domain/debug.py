"""Dev-only Debug API helpers. Spec §14.10."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import uuid
from typing import Final

from app.core.config import Settings

DEBUG_RESET_CONFIRM: Final[str] = "RESET_MY_DEBUG_ACCOUNT"
DEBUG_FAILURE_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "chat",
        "extract",
        "asr",
        "tts",
        "vision",
        "safety",
        "search",
        "storage",
        "apns",
    }
)
_LOCAL_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def debug_router_enabled(settings: Settings) -> bool:
    if settings.app_env == "prod":
        return False
    if settings.app_env == "dev":
        return True
    return settings.enable_debug_api_for_tests


def parse_debug_allowlist(raw: str) -> frozenset[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        ids.add(uuid.UUID(item))
    return frozenset(ids)


def source_host_allowed(host: str | None, *, app_env: str) -> bool:
    if not host:
        return False
    lowered = host.strip().lower()
    if lowered in _LOCAL_HOSTS:
        return app_env != "prod"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private)


def debug_token_matches(*, provided: str | None, expected: str | None) -> bool:
    if not provided or not expected:
        return False
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def debug_identity_allowed(
    *,
    user_id: uuid.UUID,
    settings: Settings,
    provided_token: str | None,
) -> bool:
    allowlist = parse_debug_allowlist(settings.debug_allowlist)
    if user_id in allowlist:
        return True
    expected = None
    if settings.debug_token is not None:
        expected = settings.debug_token.get_secret_value()
    return debug_token_matches(provided=provided_token, expected=expected)
