from __future__ import annotations

import json
from pathlib import Path

from app.domain.pact import (
    INTERVIEW_BANK_SHA256,
    INTERVIEW_BANK_VERSION,
    INTERVIEW_CATEGORIES,
    MIN_INTERVIEW_QUESTIONS,
    interview_bank_sha256,
    interview_question_ids,
    ios_interview_question_ids,
    load_interview_bank,
    question_bank_version_for,
)

_WORKSPACE = Path(__file__).resolve().parents[3]
_BANK_JSON = Path(__file__).resolve().parents[2] / "app" / "domain" / "interview_bank.json"


def test_interview_bank_is_versioned_and_has_thirty_unique_ids() -> None:
    bank = load_interview_bank()
    assert bank.version == INTERVIEW_BANK_VERSION
    assert len(bank.questions) >= MIN_INTERVIEW_QUESTIONS
    ids = [item.question_id for item in bank.questions]
    assert len(set(ids)) == len(ids)
    assert ids[:3] == ["interview-v1-q1", "interview-v1-q2", "interview-v1-q3"]
    assert {item.category for item in bank.questions} == set(INTERVIEW_CATEGORIES)
    raw = json.loads(_BANK_JSON.read_text(encoding="utf-8"))
    walked = json.dumps(raw)
    assert '"prompt"' not in walked
    assert bank.sha256 == interview_bank_sha256()
    assert len(bank.sha256) == 64


def test_interview_bank_hash_is_pinned() -> None:
    digest = interview_bank_sha256()
    assert digest == INTERVIEW_BANK_SHA256
    assert load_interview_bank().sha256 == INTERVIEW_BANK_SHA256


def test_theme_selects_server_bank_version_not_client_upload() -> None:
    assert question_bank_version_for("interview") == "interview-v1"
    assert question_bank_version_for("notes") == "notes-v1"


def test_ios_interview_ids_match_when_present() -> None:
    server_ids = set(interview_question_ids())
    ios_ids = ios_interview_question_ids(_WORKSPACE)
    if ios_ids is None:
        assert not (_WORKSPACE / "3_ios" / "Resources" / "interview.json").is_file()
        return
    assert set(ios_ids) == server_ids
