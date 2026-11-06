"""Hand a settled visit to central as a life-experience fact. No private memory."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.central import OrchestratorRequest
from app.domain.spirit_state import require_aware
from app.repositories import bootstrap as bootstrap_repo
from app.repositories.central.snapshot import LockedSpirit
from app.services.central.facade import SettlementOutcome
from app.services.central.lifecycle import settle_then_resolve
from app.services.central.pipelines import run_user_action
from app.services.room.store import save_projection

_WORKER_ROLE = "kelin_worker"
_EVENT = "visit.completed"


def _hash(visit_id: uuid.UUID) -> str:
    return hashlib.sha256(f"visit.completed:{visit_id}".encode()).hexdigest()


async def publish_visit_completed(
    factory: async_sessionmaker[AsyncSession],
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    visit_id: uuid.UUID,
    now: datetime,
) -> bool:
    """Record visit.completed. Emotion change upserts the room four-piece."""
    require_aware(now, field="now")
    user = CurrentUser(id=owner_id)
    async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
        present = await session.scalar(
            text(
                "SELECT 1 FROM public.spirits s "
                "WHERE s.id = :spirit_id AND s.user_id = :owner_id"
            ),
            {"spirit_id": spirit_id, "owner_id": owner_id},
        )
        if present is None:
            return False
        request = OrchestratorRequest(
            request_kind="user_action",
            event_id=visit_id,
            spirit_id=spirit_id,
            occurred_at=now,
            client_payload_hash=_hash(visit_id),
            current_snapshot_version=0,
            action_event_type=_EVENT,
        )

        async def apply(inner: AsyncSession, _locked: LockedSpirit) -> SettlementOutcome:
            awareness = await settle_then_resolve(
                inner,
                user,
                now=now,
            )
            room_changed = awareness.changed
            return SettlementOutcome(
                changed=room_changed,
                payload={
                    "visit_completed": True,
                    "upsert_room": room_changed,
                },
            )

        result = await run_user_action(session, user, request, legacy_settle=apply)
        if not result.replayed and result.payload.get("upsert_room"):
            rows = await bootstrap_repo.load_bootstrap_aggregate_rows(
                session, user.id, now=now, require_read_snapshot=False
            )
            if rows.spirit is not None:
                from app.services.bootstrap import room_public_from_aggregate

                await save_projection(
                    session,
                    spirit_id=spirit_id,
                    room=room_public_from_aggregate(rows, now=now),
                    snapshot_version=result.snapshot_version,
                )
    return True
