from __future__ import annotations

import asyncio
from uuid import uuid4

from app.domain.pact import (
    FALLBACK_FEEDBACK_SUMMARY,
    INCREMENTAL_FEEDBACK_SUMMARY,
    PactAnswerFeedbackValue,
    PactQuestion,
    StoredPactAnswer,
    decode_session_answers,
    encode_session_answers,
    find_stored_answer,
    incremental_answer_feedback,
    next_answer_question_id,
    pact_answer_hash,
)
from app.providers.errors import ProviderError
from app.providers.pact_feedback import (
    FailingPactFeedbackProvider,
    TemplatePactFeedbackProvider,
    resolve_pact_question_feedback,
)
from app.schemas.pact import PactAnswerRequest
from pydantic import ValidationError


def test_incremental_templates_match_fixtures() -> None:
    success = incremental_answer_feedback(fallback=False)
    assert success.summary == INCREMENTAL_FEEDBACK_SUMMARY
    assert success.improvements == ("补一个结果数字",)
    fallback = incremental_answer_feedback(fallback=True)
    assert fallback.summary == FALLBACK_FEEDBACK_SUMMARY
    assert fallback.improvements == ("用固定模板回答",)


def test_answers_must_follow_question_order() -> None:
    questions = (
        PactQuestion(question_id="interview-v1-q1", category="self_intro", text="一"),
        PactQuestion(question_id="interview-v1-q2", category="self_intro", text="二"),
        PactQuestion(question_id="interview-v1-q3", category="self_intro", text="三"),
    )
    assert next_answer_question_id(questions, ()) == "interview-v1-q1"
    first = _stored("interview-v1-q1")
    assert next_answer_question_id(questions, (first,)) == "interview-v1-q2"
    encoded = encode_session_answers((first,))
    assert "prompt" not in encoded
    decoded = decode_session_answers(encoded)
    assert find_stored_answer(decoded, question_id="interview-v1-q1") is not None
    assert find_stored_answer(decoded, question_id="interview-v1-q2") is None


def test_answer_hash_changes_with_text_or_question() -> None:
    session_id = uuid4()
    first = pact_answer_hash(session_id=session_id, question_id="interview-v1-q1", text="A")
    same = pact_answer_hash(session_id=session_id, question_id="interview-v1-q1", text="A")
    other_text = pact_answer_hash(session_id=session_id, question_id="interview-v1-q1", text="B")
    other_q = pact_answer_hash(session_id=session_id, question_id="interview-v1-q2", text="A")
    assert first == same
    assert first != other_text
    assert first != other_q


def test_answer_request_forbids_score() -> None:
    payload = {
        "client_id": str(uuid4()),
        "session_id": str(uuid4()),
        "expected_session_version": 1,
        "question_id": "interview-v1-q1",
        "text": "我想加入因为方向匹配。",
        "score": 90,
    }
    try:
        PactAnswerRequest.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("pact-answer must forbid client score")


def test_provider_failure_falls_back_to_template() -> None:
    fallback = asyncio.run(
        resolve_pact_question_feedback(
            FailingPactFeedbackProvider(),
            question_id="interview-v1-q1",
            question_text="请用两分钟介绍你自己。",
            answer_text="我想加入因为方向匹配。",
        )
    )
    assert fallback.summary == FALLBACK_FEEDBACK_SUMMARY
    success = asyncio.run(
        resolve_pact_question_feedback(
            TemplatePactFeedbackProvider(),
            question_id="interview-v1-q1",
            question_text="请用两分钟介绍你自己。",
            answer_text="我想加入因为方向匹配。",
        )
    )
    assert success.summary == INCREMENTAL_FEEDBACK_SUMMARY


def test_failing_provider_raises_model_unavailable() -> None:
    try:
        asyncio.run(
            FailingPactFeedbackProvider().question_feedback(
                question_id="interview-v1-q1",
                question_text="一",
                answer_text="答",
            )
        )
    except ProviderError as exc:
        assert exc.code == "MODEL_UNAVAILABLE"
        return
    raise AssertionError("failing provider must raise")


def _stored(question_id: str) -> StoredPactAnswer:
    return StoredPactAnswer(
        question_id=question_id,
        client_id=uuid4(),
        text="答",
        request_hash="abc",
        feedback=PactAnswerFeedbackValue(summary="方向清楚，可以再具体一点。", improvements=()),
    )
