"""Ask answer and match adapters. The answer sees the stored detail, not a live skill."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol

from app.domain.ask.prompt import DialogueRole, render_ask_prompt
from app.providers.errors import ProviderError
from app.services.ask.first_reply import AnswerCallFailed
from app.services.ask.match import MatchCallFailed

_MATCH_INSTRUCTION = (
    "从给出的技能里选出能接住这个问题的 skill_id。"
    "只返回一个 JSON 对象，键只有 skill_ids，值是字符串数组。"
    "没有合适的就返回空数组。不要回答问题本身。"
)


class AskCompletion(Protocol):
    async def complete_ask(self, instruction: str, user_text: str) -> str: ...


class OfficialCompletion(Protocol):
    async def complete_official(
        self, text: str, session_id: str | None
    ) -> tuple[str, str | None]: ...


class ProviderAsk:
    def __init__(self, provider: AskCompletion) -> None:
        self._provider = provider

    async def reply(
        self,
        *,
        detail: str,
        reference_links: Sequence[str],
        question: str,
        dialogue: Sequence[tuple[DialogueRole, str]],
        text: str,
    ) -> str:
        instruction, user_text = render_ask_prompt(
            detail=detail,
            reference_links=reference_links,
            question=question,
            dialogue=dialogue,
            text=text,
        )
        try:
            return await self._provider.complete_ask(instruction, user_text)
        except ProviderError as exc:
            raise AnswerCallFailed from exc


class ProviderOfficial:
    def __init__(self, provider: OfficialCompletion) -> None:
        self._provider = provider

    async def reply(self, *, text: str, session_id: str | None) -> tuple[str, str | None]:
        try:
            return await self._provider.complete_official(text, session_id)
        except ProviderError as exc:
            raise AnswerCallFailed from exc


class MatchCompletion(Protocol):
    async def complete_match(self, instruction: str, user_text: str) -> list[str]: ...


class ProviderMatch:
    def __init__(self, provider: MatchCompletion) -> None:
        self._provider = provider

    async def think(self, payload: dict[str, object]) -> list[str]:
        user_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            return await self._provider.complete_match(_MATCH_INSTRUCTION, user_text)
        except ProviderError as exc:
            raise MatchCallFailed from exc
