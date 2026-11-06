"""Fan out dialogue.completed after persist_reply. Chat must not ingest."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import CurrentUser
from app.domain.awareness.types import AwarenessFact
from app.domain.central.consumers import consumers_for_event
from app.services.central.lifecycle import settle_then_resolve
from app.services.room.store import persist_assembled
from app.services.memory.window_service import (
    DialogueCompletedFact,
    WindowAdvanceResult,
    on_dialogue_completed,
)

DIALOGUE_COMPLETED = "dialogue.completed"


async def publish_dialogue_completed(
    session: AsyncSession,
    user: CurrentUser,
    fact: DialogueCompletedFact,
) -> WindowAdvanceResult | None:
    consumers = consumers_for_event(DIALOGUE_COMPLETED)
    await settle_then_resolve(
        session,
        user,
        now=fact.occurred_at,
        awareness_facts=(_dialogue_life_fact(fact),),
    )
    advanced = None
    if "memory" in consumers:
        advanced = await on_dialogue_completed(session, user, fact)
    await persist_assembled(session, user.id, now=fact.occurred_at)
    return advanced


def _dialogue_life_fact(fact: DialogueCompletedFact) -> AwarenessFact:
    return AwarenessFact(
        event_id=fact.event_id,
        event_type=DIALOGUE_COMPLETED,
        occurred_at=fact.occurred_at,
        valid_interaction=True,
        accepted=True,
    )

