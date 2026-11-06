"""Central facade: lock, event_id idempotency, one snapshot_version bump."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.central import OrchestratorRequest, plan
from app.repositories.central.idempotency import claim_event, complete_event
from app.repositories.central.snapshot import LockedSpirit, bump_snapshot_once
from app.services.central.idempotency import decide_claim
from app.services.central.lock_service import lock_aggregate

SettleFn = Callable[[AsyncSession, LockedSpirit], Awaitable["SettlementOutcome"]]


@dataclass(frozen=True, slots=True)
class SettlementOutcome:
    changed: bool
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FacadeResult:
    replayed: bool
    snapshot_version: int
    bumped: bool
    payload: dict[str, Any]


async def execute(
    session: AsyncSession,
    user: CurrentUser,
    request: OrchestratorRequest,
    settle: SettleFn,
) -> FacadeResult:
    """Auth is already done. Caller must be inside claimed_transaction."""
    planned = plan(request)
    locked = await lock_aggregate(session, user.id)
    if locked is None:
        raise ApiError(
            "NOT_FOUND",
            public_error_message("NOT_FOUND"),
            status_code=404,
        )
    skip_idempotency = "idempotency" not in planned.pipeline
    if not skip_idempotency:
        claim = await claim_event(
            session, user.id, request.event_id, request.client_payload_hash
        )
        decision, stored = decide_claim(claim, request.client_payload_hash)
        if decision == "replay":
            assert stored is not None
            return FacadeResult(
                replayed=True,
                snapshot_version=int(stored["snapshot_version"]),
                bumped=False,
                payload=dict(stored.get("payload") or {}),
            )
    outcome = await settle(session, locked)
    snapshot_version = locked.snapshot_version
    bumped = False
    if outcome.changed:
        snapshot_version = await bump_snapshot_once(
            session, owner_id=user.id, spirit_id=locked.id
        )
        bumped = True
    payload = dict(outcome.payload)
    if not skip_idempotency:
        await complete_event(
            session,
            user.id,
            request.event_id,
            {
                "snapshot_version": snapshot_version,
                "bumped": bumped,
                "payload": payload,
            },
        )
    return FacadeResult(
        replayed=False,
        snapshot_version=snapshot_version,
        bumped=bumped,
        payload=payload,
    )
