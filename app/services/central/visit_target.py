"""Visit-target settle: lock the host/peer spirit and run bootstrap pipeline settle."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.central import OrchestratorRequest
from app.domain.spirit_state import require_aware
from app.repositories import bootstrap as bootstrap_repo
from app.repositories import visits as visit_repo
from app.repositories.central.snapshot import LockedSpirit
from app.services.central.facade import FacadeResult, SettlementOutcome
from app.services.central.lifecycle import settle_then_resolve
from app.services.central.pipelines import run_bootstrap

_WORKER_ROLE = "kelin_worker"


def _payload_hash(spirit_id: uuid.UUID, now: datetime) -> str:
    return hashlib.sha256(f"visit-target:{spirit_id}:{now.isoformat()}".encode()).hexdigest()


async def run_visit_target_settle(
    session: AsyncSession,
    target_owner: CurrentUser,
    *,
    now: datetime,
) -> FacadeResult | None:
    """Lock the target owner aggregate and settle(now). Does not assemble private room."""
    require_aware(now, field="now")
    peek = await bootstrap_repo.load_bootstrap_aggregate_rows(
        session, target_owner.id, now=now, require_read_snapshot=False
    )
    if peek.spirit is None:
        return None
    request = OrchestratorRequest(
        request_kind="clock_tick",
        event_id=uuid.uuid4(),
        spirit_id=peek.spirit.id,
        occurred_at=now,
        client_payload_hash=_payload_hash(peek.spirit.id, now),
        current_snapshot_version=peek.spirit.version,
    )

    async def settle(session: AsyncSession, locked: LockedSpirit) -> SettlementOutcome:
        awareness = await settle_then_resolve(session, target_owner, now=now)
        if awareness.decision is None or awareness.spirit_id != locked.id:
            return SettlementOutcome(changed=False, payload={"reason": "missing_spirit"})
        return SettlementOutcome(
            changed=awareness.changed,
            payload={
                "status": awareness.decision.status,
                "spirit_id": str(locked.id),
            },
        )

    return await run_bootstrap(session, target_owner, request, legacy_settle=settle)


async def settle_visit_targets(
    factory: async_sessionmaker[AsyncSession],
    owner_ids: tuple[uuid.UUID, ...],
    *,
    now: datetime,
) -> None:
    """Impersonate each target owner under the worker role so RLS can write awareness."""
    require_aware(now, field="now")
    seen: set[uuid.UUID] = set()
    for owner_id in owner_ids:
        if owner_id in seen:
            continue
        seen.add(owner_id)
        user = CurrentUser(id=owner_id)
        async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
            await run_visit_target_settle(session, user, now=now)


async def settle_visit_spirit_targets(
    factory: async_sessionmaker[AsyncSession],
    spirit_ids: tuple[uuid.UUID, ...],
    *,
    now: datetime,
    extra_owner_ids: tuple[uuid.UUID, ...] = (),
) -> None:
    """Resolve spirit ids via definer, then settle each owner through central."""
    require_aware(now, field="now")
    owners: list[uuid.UUID] = list(extra_owner_ids)
    seen_spirits: set[uuid.UUID] = set()
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        for spirit_id in spirit_ids:
            if spirit_id in seen_spirits:
                continue
            seen_spirits.add(spirit_id)
            owner_id = await visit_repo.fetch_spirit_owner_id(session, spirit_id)
            if owner_id is not None:
                owners.append(owner_id)
    await settle_visit_targets(factory, tuple(owners), now=now)
