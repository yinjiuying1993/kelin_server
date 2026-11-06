"""Bootstrap snapshot: facade lock, settle(now) transfer, then assemble room."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.public_map import (
    hatched_at_public,
    room_public_assembled,
    spirit_public_from_mapping,
    utc_z,
)
from app.core.config import get_settings
from app.core.security import CurrentUser
from app.domain.central import OrchestratorRequest
from app.domain.quota import QuotaSnapshot, quota_usage
from app.domain.spirit_state import require_aware
from app.repositories import bootstrap as bootstrap_repo
from app.repositories.central.snapshot import LockedSpirit
from app.schemas.bootstrap import BootstrapSnapshot, FeatureFlags
from app.schemas.room import RoomPublic
from app.schemas.spirit import SpiritPublic
from app.services.central import SettlementOutcome, run_bootstrap
from app.services.central.lifecycle import settle_then_resolve
from app.services.room.store import save_projection

_SCHEMA_VERSION: Literal[2] = 2
_API_VERSION: Literal["v1"] = "v1"


def _spirit_public(row: bootstrap_repo.BootstrapSpiritRow) -> SpiritPublic:
    return spirit_public_from_mapping(
        {
            "id": row.id,
            "name": row.name,
            "egg": row.egg,
            "invite_code": row.invite_code,
            "closeness": row.closeness,
            "curiosity": row.curiosity,
            "sharpness": row.sharpness,
            "nocturnal": row.nocturnal,
            "stubborn": row.stubborn,
            "hunger": row.hunger,
            "energy": row.energy,
            "mood": row.mood,
            "bond": row.bond,
            "stage": row.stage,
            "status": row.status,
            "version": row.version,
            "hatched_at": hatched_at_public(hatched_at=row.hatched_at, created_at=row.created_at),
            "created_at": utc_z(row.created_at),
        }
    )


def _payload_hash(spirit_id: uuid.UUID, now: datetime) -> str:
    return hashlib.sha256(f"bootstrap:{spirit_id}:{now.isoformat()}".encode()).hexdigest()


async def _legacy_settle_now(
    session: AsyncSession,
    locked: LockedSpirit,
    *,
    user: CurrentUser,
    now: datetime,
) -> SettlementOutcome:
    """Lock → awareness settle(now). Does not write stage."""
    awareness = await settle_then_resolve(session, user, now=now)
    if awareness.decision is None or awareness.spirit_id != locked.id:
        return SettlementOutcome(changed=False, payload={"reason": "missing_spirit"})
    return SettlementOutcome(
        changed=awareness.changed,
        payload={
            "status": awareness.decision.status,
            "spirit_id": str(locked.id),
        },
    )


async def load_bootstrap_snapshot(
    session: AsyncSession,
    user: CurrentUser,
    *,
    now: datetime,
    require_read_snapshot: bool = True,
    snapshot_version: int | None = None,
) -> BootstrapSnapshot:
    require_aware(now, field="now")
    settings = get_settings()
    if settings.schema_version != 2 or settings.api_version != "v1":
        raise RuntimeError("bootstrap schema/api version mismatch")
    rows = await bootstrap_repo.load_bootstrap_aggregate_rows(
        session, user.id, now=now, require_read_snapshot=require_read_snapshot
    )
    return await _assemble_snapshot(
        session, user, rows, now=now, snapshot_version=snapshot_version
    )


async def run_bootstrap_snapshot(
    session: AsyncSession,
    user: CurrentUser,
    *,
    now: datetime,
) -> BootstrapSnapshot:
    """Lock → settle(now) → assemble room. Same snapshot_version as later writes."""
    require_aware(now, field="now")
    peek = await bootstrap_repo.load_bootstrap_aggregate_rows(
        session, user.id, now=now, require_read_snapshot=False
    )
    if peek.spirit is None:
        return await _assemble_snapshot(session, user, peek, now=now, snapshot_version=0)

    request = OrchestratorRequest(
        request_kind="bootstrap",
        event_id=uuid.uuid4(),
        spirit_id=peek.spirit.id,
        occurred_at=now,
        client_payload_hash=_payload_hash(peek.spirit.id, now),
        current_snapshot_version=peek.spirit.snapshot_version,
    )

    async def settle(session: AsyncSession, locked: LockedSpirit) -> SettlementOutcome:
        return await _legacy_settle_now(session, locked, user=user, now=now)

    result = await run_bootstrap(session, user, request, legacy_settle=settle)
    snapshot = await load_bootstrap_snapshot(
        session,
        user,
        now=now,
        require_read_snapshot=False,
        snapshot_version=result.snapshot_version,
    )
    if snapshot.spirit is not None and snapshot.room is not None:
        await save_projection(
            session,
            spirit_id=snapshot.spirit.id,
            room=snapshot.room,
            snapshot_version=result.snapshot_version,
        )
    return snapshot


async def _assemble_snapshot(
    session: AsyncSession,
    user: CurrentUser,
    rows: bootstrap_repo.BootstrapAggregateRows,
    *,
    now: datetime,
    snapshot_version: int | None,
) -> BootstrapSnapshot:
    if rows.spirit is None:
        return BootstrapSnapshot(
            schema_version=_SCHEMA_VERSION,
            snapshot_version=0 if snapshot_version is None else snapshot_version,
            api_version=_API_VERSION,
            spirit=None,
            room=None,
            latest_memories=[],
            unread_postcards=[],
            quotas=[],
            feature_flags=FeatureFlags(remote_search=rows.remote_search_on),
        )

    spirit = rows.spirit
    version = spirit.snapshot_version if snapshot_version is None else snapshot_version
    return BootstrapSnapshot(
        schema_version=_SCHEMA_VERSION,
        snapshot_version=version,
        api_version=_API_VERSION,
        spirit=spirit_public_from_bootstrap_row(spirit),
        room=room_public_from_aggregate(rows, now=now),
        latest_memories=[],
        unread_postcards=[],
        quotas=[
            quota_usage(
                QuotaSnapshot(
                    capability=item.capability,
                    used=item.used,
                    reserved=0,
                    limit=item.limit,
                    usage_date=item.usage_date,
                    timezone=item.timezone,
                    version=1,
                )
            )
            for item in rows.quotas
        ],
        feature_flags=FeatureFlags(remote_search=spirit.remote_search_on),
    )


def spirit_public_from_bootstrap_row(row: bootstrap_repo.BootstrapSpiritRow) -> SpiritPublic:
    return _spirit_public(row)


def room_public_from_aggregate(
    rows: bootstrap_repo.BootstrapAggregateRows,
    *,
    now: datetime,
) -> RoomPublic:
    _ = now
    spirit = rows.spirit
    if spirit is None:
        raise RuntimeError("room projection requires a spirit")
    candidates: list[str] = []
    if rows.due_promise is not None:
        candidates.append("promise_due")
    return room_public_assembled(
        status=spirit.status,
        stage=spirit.stage,
        hunger=spirit.hunger,
        energy=spirit.energy,
        primary_emotion_label=spirit.primary_emotion_label or rows.latest_emotion,
        letter_candidates=tuple(candidates),
        image_key=spirit.stage_image_key,
    )
