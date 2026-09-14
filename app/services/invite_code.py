"""Allocate a unique invite code with SAVEPOINT retry. Spec §§6.2, 8.9."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.domain.invite_code import (
    INVITE_CODE_UNIQUE_CONSTRAINT,
    MAX_ALLOCATE_ATTEMPTS,
    generate_invite_code,
    invite_code_is_valid,
)

_LOGGER = get_logger(component="invite_code")


class InviteCodeAllocationError(Exception):
    """Unique invite_code retries exhausted. Message must not include a code."""


class SavepointSession(Protocol):
    def begin_nested(self) -> Any: ...


def is_invite_code_unique_conflict(exc: BaseException) -> bool:
    sqlstate: str | None = None
    constraint: str | None = None
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        sqlstate = _attr_str(current, "sqlstate") or _attr_str(current, "pgcode") or sqlstate
        constraint = _attr_str(current, "constraint_name") or constraint
        diag = getattr(current, "diag", None)
        if diag is not None:
            constraint = _attr_str(diag, "constraint_name") or constraint
        nxt = current.__cause__ or current.__context__
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException) and orig is not current:
            current = orig
        elif isinstance(nxt, BaseException) and nxt is not current:
            current = nxt
        else:
            current = None
    blob = str(exc).lower()
    unique = sqlstate == "23505" or "23505" in blob or "duplicate key" in blob
    if not unique:
        return False
    if constraint is not None:
        return constraint == INVITE_CODE_UNIQUE_CONSTRAINT or "invite_code" in constraint
    return "invite_code" in blob


def _attr_str(obj: object, name: str) -> str | None:
    value = getattr(obj, name, None)
    if value is None:
        return None
    text = str(value)
    return text if text else None


async def allocate_invite_code(
    session: SavepointSession,
    reserve: Callable[[str], Awaitable[None]],
    *,
    generate: Callable[[], str] = generate_invite_code,
    max_attempts: int = MAX_ALLOCATE_ATTEMPTS,
) -> str:
    """Generate a code and call reserve(); retry only on invite_code unique conflicts."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    for _ in range(max_attempts):
        code = generate()
        if not invite_code_is_valid(code):
            raise ValueError("generated invite code is invalid")
        try:
            async with session.begin_nested():
                await reserve(code)
        except IntegrityError as exc:
            if is_invite_code_unique_conflict(exc):
                continue
            raise
        return code
    _LOGGER.error("invite_code_allocation_exhausted", attempts=max_attempts)
    raise InviteCodeAllocationError("invite code allocation exhausted")
