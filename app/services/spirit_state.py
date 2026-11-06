"""Thin forward of clock settle to awareness.settle_now. Spec §§8.1, 8.6, 9.2.

Must run inside a writable claimed_transaction. Does not open REPEATABLE READ,
does not aggregate bootstrap, and does not treat bootstrap as interaction.
Does not compute 18/72 here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.domain.spirit_state import require_aware
from app.repositories import spirit_state as spirit_state_repo
from app.services.awareness import settle_now


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
    result = await settle_now(session, user, now=now)
    if result.decision is None or result.spirit_id is None:
        return SpiritSettleResult(
            spirit_id=None,
            status=None,
            version=None,
            changed=False,
            last_interact_at=None,
        )
    version = await spirit_state_repo.fetch_spirit_row_version(
        session, user.id, result.spirit_id
    )
    if not result.destination_changed:
        return SpiritSettleResult(
            spirit_id=result.spirit_id,
            status=result.decision.status,
            version=version,
            changed=False,
            last_interact_at=result.last_interact_at,
        )
    version = await spirit_state_repo.bump_spirit_row_version(
        session, user.id, result.spirit_id
    )
    await spirit_state_repo.insert_state_settle_outbox(
        session,
        user.id,
        result.spirit_id,
        from_status=result.previous_status,
        to_status=result.decision.status,
        version=version,
    )
    return SpiritSettleResult(
        spirit_id=result.spirit_id,
        status=result.decision.status,
        version=version,
        changed=True,
        last_interact_at=result.last_interact_at,
    )
