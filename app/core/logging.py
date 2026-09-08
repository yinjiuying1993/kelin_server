import json
import logging
import re
import sys
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
)

_SECRET_VALUE_RE = re.compile(
    r"(postgres(?:ql)?://\S+)|(\beyJ[\w-]+\.[\w-]+\.[\w-]+\b)|(Bearer\s+\S+)",
    re.IGNORECASE,
)

_REDACTED = "[redacted]"
_PRESENCE_VALUES = frozenset({"set", "unset"})


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        return _REDACTED
    return value


def redact_event_dict(
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    for key in list(event_dict):
        if _is_sensitive_key(key):
            if event_dict[key] in _PRESENCE_VALUES:
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
