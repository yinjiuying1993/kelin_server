from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.domain.pact import (
    SKIP_SCORE,
    next_skip_completeness,
    pact_day_is_closed,
    pact_meets_complete_rule,
    pact_skip_hash,
    score_answer_text,
    session_day_index,
)
from app.schemas.pact import PactSkipRequest
from pydantic import ValidationError


def test_skip_lowers_completeness_without_counting_completed() -> None:
    first = next_skip_completeness(previous=0, completed_sessions=0, skipped_before=0)
    assert first == SKIP_SCORE
    after_four = next_skip_completeness(previous=100, completed_sessions=4, skipped_before=0)
    assert after_four == 90
    assert after_four < 100
    assert pact_meets_complete_rule(completed_sessions=4, completeness=100) is False
    assert pact_meets_complete_rule(completed_sessions=5, completeness=69) is False
    assert pact_meets_complete_rule(completed_sessions=5, completeness=70) is True


def test_four_perfect_sessions_score_one_hundred() -> None:
    text = "因为当时结果提升了20个百分点，例如上线后转化。"
    assert score_answer_text(text) == 100


def test_skip_hash_is_pact_and_date() -> None:
    pact_id = uuid4()
    first = pact_skip_hash(pact_id=pact_id, session_date=date(2026, 9, 12))
    same = pact_skip_hash(pact_id=pact_id, session_date=date(2026, 9, 12))
    other = pact_skip_hash(pact_id=pact_id, session_date=date(2026, 9, 13))
    assert first == same
    assert first != other


def test_expired_week_is_closed() -> None:
    now = datetime(2026, 9, 12, 4, 0, tzinfo=UTC)
    ends = now + timedelta(days=7)
    assert pact_day_is_closed(now=now, ends_at=ends, status="active", day_index=1) is False
    assert pact_day_is_closed(now=ends, ends_at=ends, status="active", day_index=1) is True
    assert session_day_index(date(2026, 9, 12), date(2026, 9, 19)) is None


def test_skip_request_requires_session_date() -> None:
    payload = {"client_id": str(uuid4()), "pact_id": str(uuid4())}
    try:
        PactSkipRequest.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError("pact-skip must require session_date")
