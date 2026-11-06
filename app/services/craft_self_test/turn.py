"""One self-test turn. The reply is not stored."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.domain.craft_self_test.prompt import DialogueRole
from app.repositories.self_test import SelfTestStore
from app.services.craft_self_test.tickets import require_owned_self_test_ticket


class TurnUnit(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[SelfTestStore]: ...


class TurnModerator(Protocol):
    async def decision(self, text: str) -> Literal["allow", "block"]: ...


class TurnModel(Protocol):
    async def reply(
        self,
        *,
        snapshot_body: str,
        dialogue: Sequence[tuple[DialogueRole, str]],
        text: str,
    ) -> str: ...


async def send_self_test_turn(
    unit: TurnUnit,
    moderator: TurnModerator,
    model: TurnModel,
    *,
    owner_id: UUID,
    ticket_id: UUID,
    text: str,
    dialogue: Sequence[tuple[DialogueRole, str]] = (),
    now: datetime,
) -> str:
    utterance = _utterance(text)
    async with unit.transaction() as store:
        ticket = await require_owned_self_test_ticket(
            store,
            owner_id=owner_id,
            ticket_id=ticket_id,
            now=now,
        )
        snapshot = ticket.snapshot_body
    if await moderator.decision(utterance) == "block":
        raise ApiError(
            "MODERATION_REJECTED",
            public_error_message("MODERATION_REJECTED"),
            status_code=422,
        )
    reply = (await model.reply(snapshot_body=snapshot, dialogue=dialogue, text=utterance)).strip()
    if not reply:
        raise ApiError(
            "DEPENDENCY_UNAVAILABLE",
            public_error_message("DEPENDENCY_UNAVAILABLE"),
            status_code=503,
            retryable=True,
        )
    return reply


def _utterance(text: str) -> str:
    cleaned = text.strip()
    if not cleaned or len(cleaned) > 500:
        raise ApiError("INVALID_INPUT", public_error_message("INVALID_INPUT"), status_code=422)
    return cleaned
