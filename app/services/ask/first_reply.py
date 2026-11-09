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

    async def save_session(
        self, question_id: UUID, asker_user_id: UUID, session_id: str
    ) -> None: ...


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


class OfficialModel(Protocol):
    async def reply(self, *, text: str, session_id: str | None) -> tuple[str, str | None]: ...


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
    official_model: OfficialModel | None = None,
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
    reply, session_id = await _answer(model, official_model, opened)
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
    if session_id is not None:
        await turns.save_session(opened.question_id, asker_user_id, session_id)
    return FirstAnswer(opened.question_id, reply, answered_at)


def _usable(turn: StoredTurn | None) -> bool:
    return turn is not None and turn.status == "succeeded" and bool(turn.reply_text)


async def _answer(
    model: AnswerModel,
    official_model: OfficialModel | None,
    opened: StoredAsk,
) -> tuple[str | None, str | None]:
    if opened.official:
        return await _official_reply(official_model, opened.question_text, opened.agent_session_id)
    reply = await _reply(model, opened)
    return reply, None


async def _official_reply(
    model: OfficialModel | None,
    text: str,
    session_id: str | None,
) -> tuple[str | None, str | None]:
    if model is None:
        return None, None
    try:
        reply, session = await model.reply(text=text, session_id=session_id)
    except AnswerCallFailed:
        return None, None
    cleaned = reply.strip()
    if not cleaned:
        return None, None
    kept = session.strip() if isinstance(session, str) and session.strip() else None
    return cleaned, kept


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
