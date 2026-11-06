"""Awareness settle. Central owns the call."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.domain.awareness import AwarenessFact
from app.services.awareness import AwarenessSettleResult, settle_now


async def settle_then_resolve(
    session: AsyncSession,
    user: CurrentUser,
    *,
    now: datetime,
    awareness_facts: tuple[AwarenessFact, ...] = (),
) -> AwarenessSettleResult:
    """Lock is already held. Settle home, away, or lost only."""
    return await settle_now(session, user, now=now, facts=awareness_facts)
