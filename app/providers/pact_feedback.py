"""Pact question feedback provider. Spec §§8.8, 13.3.

Incremental answers use a template first. Provider failures fall back to the
fixed template and must not block saving the answer. Router never imports bailian.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.pact import PactAnswerFeedbackValue, incremental_answer_feedback
from app.providers.errors import ProviderError


class PactFeedbackProvider(Protocol):
    async def question_feedback(
        self,
        *,
        question_id: str,
        question_text: str,
        answer_text: str,
    ) -> PactAnswerFeedbackValue: ...


class TemplatePactFeedbackProvider:
    async def question_feedback(
        self,
        *,
        question_id: str,
        question_text: str,
        answer_text: str,
    ) -> PactAnswerFeedbackValue:
        del question_id, question_text, answer_text
        return incremental_answer_feedback(fallback=False)


class FailingPactFeedbackProvider:
    async def question_feedback(
        self,
        *,
        question_id: str,
        question_text: str,
        answer_text: str,
    ) -> PactAnswerFeedbackValue:
        del question_id, question_text, answer_text
        raise ProviderError("MODEL_UNAVAILABLE")


async def resolve_pact_question_feedback(
    provider: PactFeedbackProvider | None,
    *,
    question_id: str,
    question_text: str,
    answer_text: str,
) -> PactAnswerFeedbackValue:
    if provider is None:
        return incremental_answer_feedback(fallback=False)
    try:
        return await provider.question_feedback(
            question_id=question_id,
            question_text=question_text,
            answer_text=answer_text,
        )
    except (ProviderError, OSError, TimeoutError):
        return incremental_answer_feedback(fallback=True)
