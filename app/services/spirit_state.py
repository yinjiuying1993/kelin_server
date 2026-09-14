"""Short-write 18h/72h settle. Spec §§8.1, 8.6, 9.2.

Must run inside a writable claimed_transaction. Does not open REPEATABLE READ,
does not aggregate bootstrap, and does not treat bootstrap as interaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.domain.spirit_state import (
    InstantClock,
    desired_spirit_state_at,
    patch_equals_snapshot,
    require_aware,
)
from app.repositories import spirit_state as spirit_state_repo
from app.services.evolution import maybe_advance_stage


@dataclass(frozen=True, slots=True)
class SpiritSettleResult:
    spirit_id: uuid.UUID | None
    status: str | None
    version: int | None
    changed: bool
    last_interact_at: datetime | None


async def settle_spirit_state(
    session: AsyncSession,
    user: CurrentUser,
    *,
    now: datetime,
) -> SpiritSettleResult:
    require_aware(now, field="now")
    await spirit_state_repo.assert_writable_transaction(session)
    locked = await spirit_state_repo.lock_spirit_state_for_owner(session, user.id)
    if locked is None:
        return SpiritSettleResult(
            spirit_id=None,
            status=None,
            version=None,
            changed=False,
            last_interact_at=None,
        )
    patch = desired_spirit_state_at(locked.snapshot, InstantClock(now))
    if patch_equals_snapshot(locked.snapshot, patch):
        return SpiritSettleResult(
            spirit_id=locked.spirit_id,
            status=locked.snapshot.status,
            version=locked.version,
            changed=False,
            last_interact_at=locked.last_interact_at,
        )
    version = await spirit_state_repo.apply_spirit_state_patch(
        session,
        user.id,
        locked.spirit_id,
        patch,
        expected_version=locked.version,
    )
    await spirit_state_repo.insert_state_settle_outbox(
        session,
        user.id,
        locked.spirit_id,
        from_status=locked.snapshot.status,
        to_status=patch.status,
        version=version,
    )
    await maybe_advance_stage(session, owner_id=user.id, spirit_id=locked.spirit_id, now=now)
    return SpiritSettleResult(
        spirit_id=locked.spirit_id,
        status=patch.status,
        version=version,
        changed=True,
        last_interact_at=locked.last_interact_at,
    )
