"""Ask-skill dialogue shell. Matching does not write craft tables."""

from __future__ import annotations

from typing import NoReturn

from app.services.v3_placeholder import unavailable


def reject() -> NoReturn:
    unavailable("ask")
