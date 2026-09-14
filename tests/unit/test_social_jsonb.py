from uuid import uuid4

import pytest
from app.schemas.social import (
    parse_outbox_payload,
    parse_pact_answers,
    parse_pact_questions,
    parse_report_eligibility,
    parse_report_memory_snapshots,
    parse_visit_public_context,
)
from pydantic import ValidationError


def test_visit_public_context_only_allows_public_fields() -> None:
    parse_visit_public_context(
        {
            "title": "阴天收集者",
            "stage": "formed",
            "weather": "cloudy",
            "public_marks": ["interview-v1"],
        }
    )
    with pytest.raises(ValidationError):
        parse_visit_public_context({"summary": "私人记忆"})
    with pytest.raises(ValidationError):
        parse_visit_public_context({"latitude": 31.2, "longitude": 121.5})
    with pytest.raises(ValidationError):
        parse_visit_public_context({"stage": "egg"})


def test_pact_questions_require_stable_question_id() -> None:
    parse_pact_questions(
        [{"question_id": "interview-1", "prompt": "你最近一次把事情讲清楚是什么时候？"}]
    )
    with pytest.raises(ValidationError):
        parse_pact_questions([{"prompt": "缺 id"}])
    parse_pact_answers(
        [
            {
                "question_id": "interview-1",
                "client_id": str(uuid4()),
                "text": "项目结果提升了百分之二十",
            }
        ]
    )


def test_report_snapshots_reject_private_payloads() -> None:
    parse_report_eligibility(
        {
            "is_eligible": False,
            "eligible_at": "2026-09-14T03:46:00Z",
            "days_remaining": 4,
            "required_dialogue_rounds": 10,
            "completed_dialogue_rounds": 3,
            "dialogue_rounds_remaining": 7,
        }
    )
    parse_report_memory_snapshots([{"id": str(uuid4()), "type": "sight", "summary": "雨夜路灯"}])
    with pytest.raises(ValidationError):
        parse_report_memory_snapshots(
            [{"id": str(uuid4()), "type": "sight", "summary": "雨夜路灯", "content": "全文"}]
        )


def test_outbox_payload_rejects_sensitive_body() -> None:
    parse_outbox_payload({"resource_id": str(uuid4()), "local_date": "2026-09-08"})
    with pytest.raises(ValidationError):
        parse_outbox_payload({"text": "明信片正文"})
    with pytest.raises(ValidationError):
        parse_outbox_payload({"prompt": "system prompt"})
    with pytest.raises(ValidationError):
        parse_outbox_payload({"content": "对话"})
