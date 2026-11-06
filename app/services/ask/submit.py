"""Submit refusals. Empty, overlong, and blocked text never open a dialogue."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.core.errors import ApiError, public_error_message
from app.schemas.ask import AskSubmitResult
from app.services.ask.first_reply import AnswerModel, FirstAnswer, FirstTurns, submit_first_reply
from app.services.ask.open import AskDialogues, AskMatcher

_TEXT_LIMIT = 500


class SubmitModerator(Protocol):
    async def decision(self, text: str) -> Literal["allow", "block"]: ...


async def submit_question(
    dialogues: AskDialogues,
    matcher: AskMatcher,
    turns: FirstTurns,
    model: AnswerModel,
    moderator: SubmitModerator,
    *,
    asker_user_id: UUID,
    client_id: UUID,
    question: str,
    now: datetime,
) -> AskSubmitResult | FirstAnswer:
    text = _question(question)
    decision = await moderator.decision(text)
    if decision == "block":
        raise _error("MODERATION_REJECTED", 422)
    if decision != "allow":
        raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True)
    answer = await submit_first_reply(
        dialogues,
        matcher,
        turns,
        model,
        asker_user_id=asker_user_id,
        client_id=client_id,
        question=text,
        now=now,
    )
    if answer is not None:
        return answer
    if await dialogues.find(asker_user_id, client_id) is None:
        return AskSubmitResult(matched=False)
    raise _error("DEPENDENCY_UNAVAILABLE", 503, retryable=True)


def _question(question: str) -> str:
    text = question.strip()
    if not text or len(text) > _TEXT_LIMIT:
        raise _error("INVALID_INPUT", 422)
    return text


def _error(code: str, status_code: int, *, retryable: bool = False) -> ApiError:
    return ApiError(code, public_error_message(code), status_code=status_code, retryable=retryable)
