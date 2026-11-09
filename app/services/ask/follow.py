"""A later ask turn. Rating closes it. The answer stays on the stored snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.repositories.ask_follow import FollowDialogue
from app.repositories.ask_turns import TurnClaim
from app.services.ask.first_reply import (
    AnswerCallFailed,
    AnswerModel,
    OfficialModel,
    _official_reply,
)

_TEXT_LIMIT = 500


class FollowStore(Protocol):
    async def load(self, asker_user_id: UUID, question_id: UUID) -> FollowDialogue | None: ...


class FollowTurns(Protocol):
    async def occupy(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
    ) -> TurnClaim: ...

    async def fail(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
    ) -> None: ...

    async def succeed(
        self,
        *,
        question_id: UUID,
        asker_user_id: UUID,
        client_id: UUID,
        user_text: str,
        reply_text: str,
        answered_at: datetime,
    ) -> datetime: ...

    async def mark_history(
        self, question_id: UUID, asker_user_id: UUID, answered_at: datetime
    ) -> datetime: ...

    async def save_session(
        self, question_id: UUID, asker_user_id: UUID, session_id: str
    ) -> None: ...


class FollowModerator(Protocol):
    async def decision(self, text: str) -> Literal["allow", "block"]: ...


@dataclass(frozen=True)
class FollowReply:
    question_id: UUID
    reply_text: str
    first_reply_at: datetime


async def send_follow_up(
    dialogues: FollowStore,
    turns: FollowTurns,
    model: AnswerModel,
    moderator: FollowModerator,
    *,
    asker_user_id: UUID,
    question_id: UUID,
    client_id: UUID,
    text: str,
    now: datetime,
    official_model: OfficialModel | None = None,
) -> FollowReply:
    utterance = _utterance(text)
    dialogue = await dialogues.load(asker_user_id, question_id)
    if dialogue is None:
        raise _error("NOT_FOUND", 404)
    if dialogue.rated:
        raise _error("ASK_CLOSED", 409)
    decision = await moderator.decision(utterance)
    if decision == "block":
        raise _error("MODERATION_REJECTED", 422)
    if decision != "allow":
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True)
    claim = await turns.occupy(
        question_id=question_id,
        asker_user_id=asker_user_id,
        client_id=client_id,
        user_text=utterance,
    )
    if claim.kind == "succeeded" and claim.reply_text:
        answered_at = dialogue.first_reply_at
        if answered_at is None:
            answered_at = await turns.mark_history(question_id, asker_user_id, now)
        return FollowReply(question_id, claim.reply_text, answered_at)
    if claim.kind != "owned":
        raise _error("IDEMPOTENCY_IN_PROGRESS", 409, retryable=True)
    reply, session_id = await _answer(model, official_model, dialogue, claim.user_text)
    if reply is None:
        await turns.fail(
            question_id=question_id,
            asker_user_id=asker_user_id,
            client_id=client_id,
            user_text=claim.user_text,
        )
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True)
    answered_at = await turns.succeed(
        question_id=question_id,
        asker_user_id=asker_user_id,
        client_id=client_id,
        user_text=claim.user_text,
        reply_text=reply,
        answered_at=now,
    )
    if session_id is not None:
        await turns.save_session(question_id, asker_user_id, session_id)
    return FollowReply(question_id, reply, answered_at)


def _utterance(text: str) -> str:
    cleaned = text.strip()
    if not cleaned or len(cleaned) > _TEXT_LIMIT:
        raise _error("INVALID_INPUT", 422)
    return cleaned


async def _answer(
    model: AnswerModel,
    official_model: OfficialModel | None,
    dialogue: FollowDialogue,
    text: str,
) -> tuple[str | None, str | None]:
    if dialogue.official:
        return await _official_reply(official_model, text, dialogue.agent_session_id)
    reply = await _reply(model, dialogue, text)
    return reply, None


async def _reply(model: AnswerModel, dialogue: FollowDialogue, text: str) -> str | None:
    try:
        reply = await model.reply(
            detail=dialogue.snapshot_detail,
            reference_links=dialogue.snapshot_reference_links,
            question=dialogue.question_text,
            dialogue=dialogue.turns,
            text=text,
        )
    except AnswerCallFailed:
        return None
    cleaned = reply.strip()
    return cleaned or None


def _error(code: str, status_code: int, *, retryable: bool = False) -> ApiError:
    return ApiError(code, public_error_message(code), status_code=status_code, retryable=retryable)
