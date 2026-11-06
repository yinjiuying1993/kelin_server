"""Four request pipelines. Wrong request_kind cannot enter another entry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.domain.central import OrchestratorPlan, OrchestratorRequest, plan
from app.repositories.central.snapshot import LockedSpirit
from app.services.central.facade import FacadeResult, SettlementOutcome, execute

SettleFn = Callable[[AsyncSession, LockedSpirit], Awaitable[SettlementOutcome]]

_ENTRY_KINDS: dict[str, frozenset[str]] = {
    "bootstrap": frozenset({"bootstrap", "clock_tick"}),
    "chat": frozenset({"chat"}),
    "user_action": frozenset({"user_action"}),
    "memory_control": frozenset({"memory_control"}),
}


class PipelineKindMismatch(ValueError):
    """Raised when a request_kind is sent to the wrong pipeline entry."""

    def __init__(self, *, entry: str, actual: str) -> None:
        self.entry = entry
        self.actual = actual
        super().__init__(f"request_kind {actual!r} cannot enter {entry} pipeline")


def bind_pipeline(request: OrchestratorRequest, *, entry: str) -> OrchestratorPlan:
    allowed = _ENTRY_KINDS.get(entry)
    if allowed is None:
        raise ValueError(f"unknown pipeline entry: {entry}")
    if request.request_kind not in allowed:
        raise PipelineKindMismatch(entry=entry, actual=request.request_kind)
    return plan(request)


def _skeleton_settle(planned: OrchestratorPlan) -> SettleFn:
    async def settle(_session: AsyncSession, locked: LockedSpirit) -> SettlementOutcome:
        return SettlementOutcome(
            changed=False,
            payload={
                "request_kind": planned.request_kind,
                "pipeline": list(planned.pipeline),
                "reason_code": planned.reason_code,
                "spirit_id": str(locked.id),
            },
        )

    return settle


async def _run(
    session: AsyncSession,
    user: CurrentUser,
    request: OrchestratorRequest,
    *,
    entry: str,
    legacy_settle: SettleFn | None,
) -> FacadeResult:
    planned = bind_pipeline(request, entry=entry)
    settle = legacy_settle if legacy_settle is not None else _skeleton_settle(planned)
    return await execute(session, user, request, settle)


async def run_bootstrap(
    session: AsyncSession,
    user: CurrentUser,
    request: OrchestratorRequest,
    *,
    legacy_settle: SettleFn | None = None,
) -> FacadeResult:
    return await _run(session, user, request, entry="bootstrap", legacy_settle=legacy_settle)


async def run_chat(
    session: AsyncSession,
    user: CurrentUser,
    request: OrchestratorRequest,
    *,
    legacy_settle: SettleFn | None = None,
) -> FacadeResult:
    return await _run(session, user, request, entry="chat", legacy_settle=legacy_settle)


async def run_user_action(
    session: AsyncSession,
    user: CurrentUser,
    request: OrchestratorRequest,
    *,
    legacy_settle: SettleFn | None = None,
) -> FacadeResult:
    return await _run(session, user, request, entry="user_action", legacy_settle=legacy_settle)


async def run_memory_control(
    session: AsyncSession,
    user: CurrentUser,
    request: OrchestratorRequest,
    *,
    legacy_settle: SettleFn | None = None,
) -> FacadeResult:
    return await _run(
        session, user, request, entry="memory_control", legacy_settle=legacy_settle
    )
