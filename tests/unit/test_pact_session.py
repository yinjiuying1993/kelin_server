from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from json import dumps
from uuid import uuid4

from app.domain.pact import (
    decode_session_questions,
    encode_session_questions,
    interview_session_questions,
    notes_session_questions,
    pact_day_is_closed,
    pact_session_hash,
    session_date_for,
    session_day_index,
    session_explain,
)
from app.schemas.pact import PactSessionRequest
from pydantic import ValidationError


def test_interview_session_picks_exactly_three_unique_ids() -> None:
    questions = interview_session_questions(1)
    assert len(questions) == 3
    ids = [item.question_id for item in questions]
    assert ids == ["interview-v1-q1", "interview-v1-q2", "interview-v1-q3"]
    assert len(set(ids)) == 3
    encoded = encode_session_questions(questions)
    assert "prompt" not in encoded
    decoded = decode_session_questions(encoded)
    assert [item.question_id for item in decoded] == ids
    assert session_explain("interview", questions) == "今天先把自我介绍说清楚。"


def test_interview_day_seven_still_exactly_three() -> None:
    questions = interview_session_questions(7)
    assert len(questions) == 3
    ids = [item.question_id for item in questions]
    assert ids == ["interview-v1-q19", "interview-v1-q20", "interview-v1-q21"]
    assert len(set(ids)) == 3


def test_notes_session_falls_back_to_three_templates() -> None:
    questions = notes_session_questions(2, None)
    assert len(questions) == 3
    ids = [item.question_id for item in questions]
    assert ids == ["notes-v1-d2-q1", "notes-v1-d2-q2", "notes-v1-d2-q3"]
    encoded = encode_session_questions(questions)
    assert "prompt" not in encoded
    assert all(item.text for item in questions)
    with_summary = notes_session_questions(1, "自定义笔记正文")
    assert "自定义笔记正文" in with_summary[0].text
    assert session_explain("notes", questions) == "今天先把这篇笔记讲清楚。"


def test_session_date_uses_server_timezone_not_client_clock() -> None:
    now = datetime(2026, 9, 12, 16, 0, tzinfo=UTC)
    pact_id = uuid4()
    assert session_date_for(now, "Asia/Shanghai") == date(2026, 9, 13)
    assert session_date_for(now, "America/Los_Angeles") == date(2026, 9, 12)
    first = pact_session_hash(pact_id=pact_id, session_date=date(2026, 9, 12))
    second = pact_session_hash(pact_id=pact_id, session_date=date(2026, 9, 13))
    assert first != second


def test_day_index_and_closed_week() -> None:
    week_start = date(2026, 9, 12)
    assert session_day_index(week_start, date(2026, 9, 12)) == 1
    assert session_day_index(week_start, date(2026, 9, 18)) == 7
    assert session_day_index(week_start, date(2026, 9, 19)) is None
    now = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
    ends_at = datetime(2026, 9, 19, 4, 0, tzinfo=UTC)
    assert pact_day_is_closed(now=now, ends_at=ends_at, status="active", day_index=7)
    assert (
        pact_day_is_closed(
            now=now - timedelta(seconds=1),
            ends_at=ends_at,
            status="active",
            day_index=7,
        )
        is False
    )
    assert pact_day_is_closed(now=now, ends_at=ends_at, status="completed", day_index=1)


def test_session_request_forbids_client_session_date() -> None:
    payload = {
        "client_id": str(uuid4()),
        "pact_id": str(uuid4()),
        "session_date": "2026-09-08",
    }
    try:
        PactSessionRequest.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("pact-session must ignore device date")


def test_decode_rejects_prompt_key() -> None:
    try:
        decode_session_questions(
            dumps(
                [
                    {"question_id": "interview-v1-q1", "prompt": "泄漏"},
                    {"question_id": "interview-v1-q2", "text": "二"},
                    {"question_id": "interview-v1-q3", "text": "三"},
                ]
            )
        )
    except RuntimeError:
        return
    raise AssertionError("stored questions must not use prompt")
