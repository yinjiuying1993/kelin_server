"""First answer for one submit key. A failed reply keeps the snapshot."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.ask.prompt import DialogueRole
from app.repositories.ask import StoredAsk
from app.repositories.ask_turns import StoredTurn
from app.services.ask.open import AskDialogues, AskMatcher, open_matched_dialogue


class AnswerCallFailed(Exception):
    def __init__(self) -> None:
        super().__init__("answer model unavailable")


@dataclass(frozen=True)
class FirstAnswer:
    question_id: UUID
    reply_text: str
    first_reply_at: datetime


class FirstTurns(Protocol):
    async def find(
        self, question_id: UUID, asker_user_id: UUID, client_id: UUID
    ) -> StoredTurn | None: ...

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


class AnswerModel(Protocol):
    async def reply(
        self,
        *,
        detail: str,
        reference_links: Sequence[str],
        question: str,
        dialogue: Sequence[tuple[DialogueRole, str]],
        text: str,
    ) -> str: ...


async def submit_first_reply(
    dialogues: AskDialogues,
    matcher: AskMatcher,
    turns: FirstTurns,
    model: AnswerModel,
    *,
    asker_user_id: UUID,
    client_id: UUID,
    question: str,
    now: datetime,
) -> FirstAnswer | None:
    opened = await open_matched_dialogue(
        dialogues,
        matcher,
        asker_user_id=asker_user_id,
        client_id=client_id,
        question=question,
    )
    if opened is None:
        return None
    turn = await turns.find(opened.question_id, asker_user_id, client_id)
    if _usable(turn):
        assert turn is not None
        assert turn.reply_text is not None
        answered_at = opened.first_reply_at
        if answered_at is None:
            answered_at = await turns.mark_history(opened.question_id, asker_user_id, now)
        return FirstAnswer(opened.question_id, turn.reply_text, answered_at)
    reply = await _reply(model, opened)
    if reply is None:
        await turns.fail(
            question_id=opened.question_id,
            asker_user_id=asker_user_id,
            client_id=client_id,
            user_text=opened.question_text,
        )
        return None
    answered_at = await turns.succeed(
        question_id=opened.question_id,
        asker_user_id=asker_user_id,
        client_id=client_id,
        user_text=opened.question_text,
        reply_text=reply,
        answered_at=now,
    )
    return FirstAnswer(opened.question_id, reply, answered_at)


def _usable(turn: StoredTurn | None) -> bool:
    return turn is not None and turn.status == "succeeded" and bool(turn.reply_text)


async def _reply(model: AnswerModel, opened: StoredAsk) -> str | None:
    try:
        text = await model.reply(
            detail=opened.snapshot_detail,
            reference_links=opened.snapshot_reference_links,
            question=opened.question_text,
            dialogue=(),
            text=opened.question_text,
        )
    except AnswerCallFailed:
        return None
    cleaned = text.strip()
    return cleaned or None
