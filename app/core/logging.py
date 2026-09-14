import hashlib
import json
import logging
import re
import sys
import uuid
from collections.abc import Mapping, MutableMapping
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings

_SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "authorization",
    "api_key",
    "private_key",
    "database_url",
    "dsn",
    "jwt",
    "credential",
    "workspace_id",
    "app_id",
    "invite_code",
    "consent",
    "document_version",
    "summary",
    "signed_url",
    "tts_voice",
    "latitude",
    "longitude",
    "gps",
    "coordinate",
    "address",
)

_SECRET_VALUE_RE = re.compile(
    r"(postgres(?:ql)?://\S+)|(\beyJ[\w-]+\.[\w-]+\.[\w-]+\b)|(Bearer\s+\S+)",
    re.IGNORECASE,
)
_INVITE_CODE_VALUE_RE = re.compile(r"(?<![A-Z0-9])[A-HJ-NP-Z2-9]{8}(?![A-Z0-9])")

_REDACTED = "[redacted]"
_PRESENCE_VALUES = frozenset({"set", "unset"})


def hash_user_id(user_id: uuid.UUID) -> str:
    return hashlib.sha256(str(user_id).encode("ascii")).hexdigest()


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _redact_value(value: Any) -> Any:
    if isinstance(value, MutableMapping):
        return redact_event_dict(value)
    if isinstance(value, Mapping):
        return redact_event_dict(dict(value))
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, str):
        if _SECRET_VALUE_RE.search(value):
            return _REDACTED
        if _INVITE_CODE_VALUE_RE.search(value):
            return _INVITE_CODE_VALUE_RE.sub(_REDACTED, value)
    return value


def redact_event_dict(
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if _is_sensitive_key(key):
            current = event_dict[key]
            if isinstance(current, str) and current in _PRESENCE_VALUES:
                continue
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _redact_value(event_dict[key])
    return event_dict


class RedactingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "event": record.getMessage(),
            "level": record.levelname.lower(),
            "timestamp": datetime.now(UTC).isoformat(),
            "logger": record.name,
        }
        extra = getattr(record, "kelin_fields", None)
        if isinstance(extra, Mapping):
            payload.update(redact_event_dict(dict(extra)))
        return json.dumps(payload, ensure_ascii=True)


class KelinLogger:
    def __init__(self, binds: Mapping[str, str]) -> None:
        self._binds = {"service": "kelin-api", **binds}
        self._logger = logging.getLogger("kelin")

    def _log(self, level: int, event: str, fields: Mapping[str, Any]) -> None:
        merged = {**self._binds, **fields}
        self._logger.log(level, event, extra={"kelin_fields": merged})

    def info(self, event: str, **fields: Any) -> None:
        self._log(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._log(logging.WARNING, event, fields)

    def error(self, event: str, **fields: Any) -> None:
        self._log(logging.ERROR, event, fields)


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logger = logging.getLogger("kelin")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingJsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(**binds: str) -> KelinLogger:
    return KelinLogger(binds)
