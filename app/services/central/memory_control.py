"""Memory control enters the central pipeline and bumps snapshot immediately."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, public_error_message
from app.core.security import CurrentUser
from app.domain.central import OrchestratorRequest
from app.domain.memory_mutation import (
    clear_request_hash,
    delete_request_hash,
    patch_request_hash,
)
from app.domain.spirit_state import require_aware
from app.repositories import memory as memory_repo
from app.repositories.central.snapshot import LockedSpirit
from app.schemas.memory import MemoryClearRequest, MemoryDeleteRequest, MemoryPatchRequest
from app.services.central.facade import SettlementOutcome
from app.services.central.pipelines import run_memory_control
from app.services.memory.control_service import settlement_from_payload, settlement_payload
from app.services.memory.legacy import (
    MemoryMutationSettlement,
    clear_owned_memories,
    delete_owned_memory,
    patch_owned_memory,
)

_EVENT_BY_ACTION = {
    "correct": "memory.corrected",
    "seal": "memory.sealed",
    "delete": "memory.deleted",
    "clear": "memory.cleared",
}


@dataclass(frozen=True, slots=True)
class MemoryControlResult:
    snapshot_version: int
    settlement: MemoryMutationSettlement


async def execute_memory_control(
    session: AsyncSession,
    user: CurrentUser,
    body: MemoryPatchRequest | MemoryDeleteRequest | MemoryClearRequest,
    *,
    memory_id: UUID | None,
    now: datetime,
) -> MemoryControlResult:
    require_aware(now, field="now")
    spirit_id = await memory_repo.fetch_owned_spirit_id(session, user.id)
    if spirit_id is None:
        raise ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)
    action, request_hash = _action_and_hash(body, memory_id)
    request = OrchestratorRequest(
        request_kind="memory_control",
        event_id=body.client_id,
        spirit_id=spirit_id,
        occurred_at=now,
        client_payload_hash=request_hash,
        current_snapshot_version=1,
        action_event_type=_EVENT_BY_ACTION[action],
        memory_cleared=action == "clear",
    )

    async def apply(inner: AsyncSession, _locked: LockedSpirit) -> SettlementOutcome:
        settled = await _apply(inner, user, body, memory_id=memory_id)
        return SettlementOutcome(changed=settled.changed, payload=settlement_payload(settled))

    facade = await run_memory_control(session, user, request, legacy_settle=apply)
    return MemoryControlResult(
        snapshot_version=facade.snapshot_version,
        settlement=settlement_from_payload(
            facade.payload, snapshot_version=facade.snapshot_version
        ),
    )


def _action_and_hash(
    body: MemoryPatchRequest | MemoryDeleteRequest | MemoryClearRequest,
    memory_id: UUID | None,
) -> tuple[str, str]:
    if isinstance(body, MemoryPatchRequest):
        if memory_id is None:
            raise ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)
        return body.action, patch_request_hash(memory_id, body)
    if isinstance(body, MemoryDeleteRequest):
        if memory_id is None:
            raise ApiError("NOT_FOUND", public_error_message("NOT_FOUND"), status_code=404)
        return "delete", delete_request_hash(memory_id, body)
    return "clear", clear_request_hash(body)


async def _apply(
    session: AsyncSession,
    user: CurrentUser,
    body: MemoryPatchRequest | MemoryDeleteRequest | MemoryClearRequest,
    *,
    memory_id: UUID | None,
) -> MemoryMutationSettlement:
    if isinstance(body, MemoryPatchRequest):
        assert memory_id is not None
        return await patch_owned_memory(session, user, memory_id, body, commit_snapshot=False)
    if isinstance(body, MemoryDeleteRequest):
        assert memory_id is not None
        return await delete_owned_memory(session, user, memory_id, body, commit_snapshot=False)
    return await clear_owned_memories(session, user, body, commit_snapshot=False)
