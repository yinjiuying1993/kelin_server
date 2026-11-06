"""Shared 501 for V3 shells. No database and no model calls."""

from __future__ import annotations

from typing import NoReturn

from app.core.errors import ApiError


def unavailable(module: str) -> NoReturn:
    raise ApiError(
        "NOT_IMPLEMENTED",
        "not implemented",
        status_code=501,
        details={"module": module},
    )
