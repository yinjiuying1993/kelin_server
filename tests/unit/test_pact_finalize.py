from __future__ import annotations

import json
from uuid import uuid4

from app.domain.pact import (
    FINALIZE_FEEDBACK_SUMMARY,
    MISTAKE_RESULT_SUMMARY,
    PACT_COMPLETE_COMPLETENESS_MIN,
    PACT_COMPLETE_SESSION_MIN,
    PactAnswerFeedbackValue,
    PactQuestion,
    StoredPactAnswer,
    answer_mistake,
    encode_session_feedback,
    next_completeness,
    pact_meets_complete_rule,
    scholar_mark_for_pact,
    score_answer_text,
    session_score,
)


def test_score_and_mistake_use_server_text_only() -> None:
    short = score_answer_text("短")
    assert short == 50
    numbered = score_answer_text("因为当时结果提升了20个百分点，例如上线后转化。")
    assert numbered == 100
    vague = score_answer_text("我想加入因为方向匹配。")
    assert vague == 70
    question = PactQuestion(question_id="interview-v1-q1", category="self_intro", text="一")
    stored = _stored("interview-v1-q1", "我想加入因为方向匹配。")
    mistake = answer_mistake(question, stored)
    assert mistake == ("result", MISTAKE_RESULT_SUMMARY)
    strong = _stored("interview-v1-q1", "因为当时结果提升了20个百分点，例如上线后转化。")
    assert answer_mistake(question, strong) is None
    encoded = encode_session_feedback(FINALIZE_FEEDBACK_SUMMARY)
    assert "prompt" not in encoded
    assert json.loads(encoded)["summary"] == FINALIZE_FEEDBACK_SUMMARY


def test_session_score_is_mean_of_three_answers() -> None:
    answers = (
        _stored("interview-v1-q1", "我想加入因为方向匹配。"),
        _stored("interview-v1-q2", "我想加入因为方向匹配。"),
        _stored("interview-v1-q3", "我想加入因为方向匹配。"),
    )
    assert session_score(answers) == 70


def test_completeness_and_complete_rule() -> None:
    first = next_completeness(previous=0, completed_before=0, scored=70)
    assert first == 70
    fifth = next_completeness(previous=70, completed_before=4, scored=70)
    assert fifth == 70
    assert pact_meets_complete_rule(completed_sessions=4, completeness=100) is False
    assert pact_meets_complete_rule(completed_sessions=5, completeness=69) is False
    assert pact_meets_complete_rule(
        completed_sessions=PACT_COMPLETE_SESSION_MIN,
        completeness=PACT_COMPLETE_COMPLETENESS_MIN,
    )
    assert scholar_mark_for_pact(theme="interview", question_bank_version="interview-v1") == (
        "interview-v1"
    )
    assert scholar_mark_for_pact(theme="interview", question_bank_version="v1") == "interview-v1"


def _stored(question_id: str, text: str) -> StoredPactAnswer:
    return StoredPactAnswer(
        question_id=question_id,
        client_id=uuid4(),
        text=text,
        request_hash="abc",
        feedback=PactAnswerFeedbackValue(summary=FINALIZE_FEEDBACK_SUMMARY, improvements=()),
    )
