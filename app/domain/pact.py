"""Pact create/session constants and interview bank. Spec §§4.5, 8.8, 13.1–13.2.

Clients never upload a question bank. interview-v1 is the server image.
Session date is server timezone/day; device clocks cannot change it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from app.domain.bootstrap import local_date, utc_z
from app.domain.evolution import pact_scholar_mark_key, require_scholar_mark_key
from app.domain.quota import coerce_timezone

PACT_CREATE_OPERATION = "pacts.create"
PACT_SESSION_OPERATION = "pacts.session"
PACT_ANSWER_OPERATION = "pacts.answer"
PACT_SKIP_OPERATION = "pacts.skip"
PACT_CREATED_EVENT = "pact.created"
PACT_ANSWERED_EVENT = "pact.answered"
PACT_COMPLETED_EVENT = "pact.completed"
PACT_ANSWERED_GROWTH = "pact_answered"
PACT_COMPLETED_GROWTH = "pact_completed"
PACT_COMPLETE_SESSION_MIN = 5
PACT_COMPLETE_COMPLETENESS_MIN = 70
PACT_COMPLETE_BOND_DELTA = 5
SKIP_SCORE = 50
FINALIZE_FEEDBACK_SUMMARY = "三题已齐，本场结束。"
MISTAKE_RESULT_SUMMARY = "结果不够具体"
INCREMENTAL_FEEDBACK_SUMMARY = "方向清楚，可以再具体一点。"
INCREMENTAL_FEEDBACK_IMPROVEMENTS = ("补一个结果数字",)
FALLBACK_FEEDBACK_SUMMARY = "先把这件事讲完整，再补一句结果。"
FALLBACK_FEEDBACK_IMPROVEMENTS = ("用固定模板回答",)
PACT_ACTIVE_UNIQUE = "uq_pacts_spirit_one_active"
PACT_SESSION_DATE_UNIQUE = "uq_pact_sessions_pact_id_session_date"
PACT_SESSION_CLIENT_UNIQUE = "uq_pact_sessions_pact_id_client_id"
PACT_DURATION = timedelta(days=7)
SESSION_QUESTION_COUNT = 3
INTERVIEW_BANK_VERSION = "interview-v1"
NOTES_BANK_VERSION = "notes-v1"
_INTERVIEW_EXPLAIN = {
    "self_intro": "今天先把自我介绍说清楚。",
    "strengths": "今天先把优势和短板说清楚。",
    "project": "今天先把项目经历说清楚。",
    "conflict": "今天先把冲突处理说清楚。",
    "star": "今天先把 STAR 结构说清楚。",
}
NOTES_EXPLAIN = "今天先把这篇笔记讲清楚。"
_NOTES_FALLBACK_TEXTS = (
    "用自己的话讲一遍这篇笔记的核心。",
    "别人最可能追问哪一点，你怎么答？",
    "举一个能证明你理解这篇笔记的例子。",
)
INTERVIEW_CATEGORIES = (
    "self_intro",
    "strengths",
    "project",
    "conflict",
    "star",
)
MIN_INTERVIEW_QUESTIONS = 30
INTERVIEW_BANK_SHA256 = "ece1b114f5017149ddf0b93bc46951f3858205ae0e038df3a9606ab151a7cab4"
_BANK_PATH = Path(__file__).with_name("interview_bank.json")
_IOS_BANK_CANDIDATES = (
    Path("3_ios") / "Resources" / "interview.json",
    Path("3_ios") / "Kelin" / "Resources" / "interview.json",
)


@dataclass(frozen=True, slots=True)
class PactQuestion:
    question_id: str
    category: str
    text: str


@dataclass(frozen=True, slots=True)
class PactAnswerFeedbackValue:
    summary: str
    improvements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoredPactAnswer:
    question_id: str
    client_id: UUID
    text: str
    request_hash: str
    feedback: PactAnswerFeedbackValue


@dataclass(frozen=True, slots=True)
class InterviewBank:
    version: str
    questions: tuple[PactQuestion, ...]
    sha256: str


def question_bank_version_for(theme: str) -> str:
    if theme == "notes":
        return NOTES_BANK_VERSION
    return INTERVIEW_BANK_VERSION


def pact_create_hash(*, theme: str, title: str, notes_memory_id: UUID | None) -> str:
    canonical = json.dumps(
        {
            "notes_memory_id": None if notes_memory_id is None else str(notes_memory_id),
            "theme": theme,
            "title": title,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pact_session_hash(*, pact_id: UUID, session_date: date) -> str:
    canonical = json.dumps(
        {"pact_id": str(pact_id), "session_date": session_date.isoformat()},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pact_skip_hash(*, pact_id: UUID, session_date: date) -> str:
    return pact_session_hash(pact_id=pact_id, session_date=session_date)


def week_window(now: datetime, timezone_name: str) -> tuple[datetime, datetime, date]:
    starts_at = now
    ends_at = now + PACT_DURATION
    week_start = local_date(now, coerce_timezone(timezone_name))
    return starts_at, ends_at, week_start


def session_date_for(now: datetime, timezone_name: str) -> date:
    return local_date(now, coerce_timezone(timezone_name))


def session_day_index(week_start: date, session_date: date) -> int | None:
    index = (session_date - week_start).days + 1
    if 1 <= index <= 7:
        return index
    return None


def pact_day_is_closed(
    *,
    now: datetime,
    ends_at: datetime,
    status: str,
    day_index: int | None,
) -> bool:
    if status != "active":
        return True
    if day_index is None:
        return True
    return now >= ends_at


def interview_session_questions(day_index: int) -> tuple[PactQuestion, ...]:
    bank = load_interview_bank()
    start = (day_index - 1) * SESSION_QUESTION_COUNT
    picked = bank.questions[start : start + SESSION_QUESTION_COUNT]
    if len(picked) != SESSION_QUESTION_COUNT:
        raise RuntimeError("interview session must pick exactly 3 questions")
    ids = [item.question_id for item in picked]
    if len(set(ids)) != SESSION_QUESTION_COUNT:
        raise RuntimeError("interview session question_id must be unique")
    return picked


def notes_session_questions(day_index: int, summary: str | None) -> tuple[PactQuestion, ...]:
    texts = list(_NOTES_FALLBACK_TEXTS)
    clipped = (summary or "").strip()
    if clipped:
        snippet = clipped[:40]
        texts[0] = f"用自己的话讲一遍这篇笔记的核心：（{snippet}）"
    questions: list[PactQuestion] = []
    for index, text in enumerate(texts, start=1):
        questions.append(
            PactQuestion(
                question_id=f"notes-v1-d{day_index}-q{index}",
                category="notes",
                text=text[:500],
            )
        )
    if len(questions) != SESSION_QUESTION_COUNT:
        raise RuntimeError("notes session must pick exactly 3 questions")
    return tuple(questions)


def session_explain(theme: str, questions: tuple[PactQuestion, ...]) -> str:
    if theme == "notes":
        return NOTES_EXPLAIN
    category = questions[0].category if questions else "self_intro"
    return _INTERVIEW_EXPLAIN.get(category, "今天先把这三道题说清楚。")


def encode_session_questions(questions: tuple[PactQuestion, ...]) -> str:
    if len(questions) != SESSION_QUESTION_COUNT:
        raise RuntimeError("session questions must be exactly 3")
    payload = []
    for item in questions:
        if not item.question_id or not item.text:
            raise RuntimeError("session question requires question_id and text")
        payload.append(
            {
                "category": item.category,
                "question_id": item.question_id,
                "text": item.text,
            }
        )
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def decode_session_questions(value: object) -> tuple[PactQuestion, ...]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or len(value) != SESSION_QUESTION_COUNT:
        raise RuntimeError("stored session questions must be exactly 3")
    questions: list[PactQuestion] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise RuntimeError("stored session question must be an object")
        if "prompt" in item:
            raise RuntimeError("stored session questions must not use prompt keys")
        question_id = str(item.get("question_id", ""))
        text = str(item.get("text", "")).strip()
        category = str(item.get("category", "interview"))
        if not question_id or question_id in seen or not text:
            raise RuntimeError("stored session question_id and text are required")
        seen.add(question_id)
        questions.append(PactQuestion(question_id=question_id, category=category, text=text))
    return tuple(questions)


def pact_answer_hash(*, session_id: UUID, question_id: str, text: str) -> str:
    canonical = json.dumps(
        {
            "question_id": question_id,
            "session_id": str(session_id),
            "text": text,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def incremental_answer_feedback(*, fallback: bool) -> PactAnswerFeedbackValue:
    if fallback:
        return PactAnswerFeedbackValue(
            summary=FALLBACK_FEEDBACK_SUMMARY,
            improvements=FALLBACK_FEEDBACK_IMPROVEMENTS,
        )
    return PactAnswerFeedbackValue(
        summary=INCREMENTAL_FEEDBACK_SUMMARY,
        improvements=INCREMENTAL_FEEDBACK_IMPROVEMENTS,
    )


def next_answer_question_id(
    questions: tuple[PactQuestion, ...], answers: tuple[StoredPactAnswer, ...]
) -> str | None:
    if len(answers) >= SESSION_QUESTION_COUNT:
        return None
    return questions[len(answers)].question_id


def find_stored_answer(
    answers: tuple[StoredPactAnswer, ...], *, question_id: str
) -> StoredPactAnswer | None:
    for item in answers:
        if item.question_id == question_id:
            return item
    return None


def encode_session_answers(answers: tuple[StoredPactAnswer, ...]) -> str:
    payload = []
    for item in answers:
        payload.append(
            {
                "client_id": str(item.client_id),
                "feedback": {
                    "improvements": list(item.feedback.improvements),
                    "summary": item.feedback.summary,
                },
                "question_id": item.question_id,
                "request_hash": item.request_hash,
                "text": item.text,
            }
        )
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def decode_session_answers(value: object) -> tuple[StoredPactAnswer, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise RuntimeError("stored session answers must be an array")
    if len(value) > SESSION_QUESTION_COUNT:
        raise RuntimeError("stored session answers exceed 3")
    answers: list[StoredPactAnswer] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise RuntimeError("stored session answer must be an object")
        if "prompt" in item:
            raise RuntimeError("stored session answers must not use prompt keys")
        question_id = str(item.get("question_id", ""))
        text = str(item.get("text", ""))
        request_hash = str(item.get("request_hash", ""))
        raw_client = item.get("client_id")
        raw_feedback = item.get("feedback")
        if not question_id or question_id in seen or not text or not request_hash:
            raise RuntimeError("stored session answer is incomplete")
        if not isinstance(raw_client, str) or not raw_client:
            raise RuntimeError("stored session answer client_id is required")
        if not isinstance(raw_feedback, dict) or "prompt" in raw_feedback:
            raise RuntimeError("stored session answer feedback is invalid")
        summary = str(raw_feedback.get("summary", "")).strip()
        raw_improvements = raw_feedback.get("improvements", [])
        if not summary or not isinstance(raw_improvements, list):
            raise RuntimeError("stored session answer feedback is invalid")
        improvements = tuple(str(entry) for entry in raw_improvements if str(entry))
        seen.add(question_id)
        answers.append(
            StoredPactAnswer(
                question_id=question_id,
                client_id=UUID(raw_client),
                text=text,
                request_hash=request_hash,
                feedback=PactAnswerFeedbackValue(summary=summary, improvements=improvements),
            )
        )
    return tuple(answers)


def score_answer_text(text: str) -> int:
    stripped = text.strip()
    score = 50
    if len(stripped) >= 8:
        score += 10
    if len(stripped) >= 16:
        score += 10
    if any(character.isdigit() for character in stripped):
        score += 15
    if any(token in stripped for token in ("因为", "所以", "结果", "例如", "当时")):
        score += 10
    if len(stripped) >= 24:
        score += 5
    return min(100, score)


def session_score(answers: tuple[StoredPactAnswer, ...]) -> int:
    if not answers:
        return 0
    total = sum(score_answer_text(item.text) for item in answers)
    return int(round(total / len(answers)))


def answer_mistake(question: PactQuestion, answer: StoredPactAnswer) -> tuple[str, str] | None:
    scored = score_answer_text(answer.text)
    has_digit = any(character.isdigit() for character in answer.text)
    if scored >= 80 and has_digit:
        return None
    if not has_digit:
        return ("result", MISTAKE_RESULT_SUMMARY)
    return (question.category or "answer", "回答还可以再展开")


def next_completeness(*, previous: int, completed_before: int, scored: int) -> int:
    count = completed_before + 1
    return int(round((previous * completed_before + scored) / count))


def next_skip_completeness(*, previous: int, completed_sessions: int, skipped_before: int) -> int:
    return next_completeness(
        previous=previous,
        completed_before=completed_sessions + skipped_before,
        scored=SKIP_SCORE,
    )


def pact_meets_complete_rule(*, completed_sessions: int, completeness: int) -> bool:
    return (
        completed_sessions >= PACT_COMPLETE_SESSION_MIN
        and completeness >= PACT_COMPLETE_COMPLETENESS_MIN
    )


def scholar_mark_for_pact(*, theme: str, question_bank_version: str) -> str:
    prefix = f"{theme}-"
    version = question_bank_version
    if version.startswith(prefix):
        version = version[len(prefix) :]
    try:
        return pact_scholar_mark_key(theme=theme, question_bank_version=version)
    except ValueError:
        return require_scholar_mark_key(question_bank_version)


def encode_session_feedback(summary: str, improvements: tuple[str, ...] = ()) -> str:
    return json.dumps(
        {"improvements": list(improvements), "summary": summary},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def utc_stamp(value: datetime) -> str:
    return utc_z(value)


@lru_cache(maxsize=1)
def load_interview_bank() -> InterviewBank:
    payload = json.loads(_BANK_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("interview bank must be an object")
    version = str(payload.get("version", ""))
    if version != INTERVIEW_BANK_VERSION:
        raise RuntimeError("interview bank version must be interview-v1")
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        raise RuntimeError("interview bank questions must be an array")
    questions: list[PactQuestion] = []
    seen: set[str] = set()
    for item in raw_questions:
        if not isinstance(item, dict):
            raise RuntimeError("interview bank question must be an object")
        if "prompt" in item:
            raise RuntimeError("interview bank must not use prompt keys")
        question_id = str(item.get("question_id", ""))
        category = str(item.get("category", ""))
        text = str(item.get("text", "")).strip()
        if not question_id or question_id in seen:
            raise RuntimeError("interview bank question_id must be unique")
        if category not in INTERVIEW_CATEGORIES:
            raise RuntimeError("interview bank category is unknown")
        if not text:
            raise RuntimeError("interview bank text is required")
        seen.add(question_id)
        questions.append(PactQuestion(question_id=question_id, category=category, text=text))
    if len(questions) < MIN_INTERVIEW_QUESTIONS:
        raise RuntimeError("interview bank must have at least 30 questions")
    digest = interview_bank_sha256(payload)
    if digest != INTERVIEW_BANK_SHA256:
        raise RuntimeError("interview bank hash mismatch")
    return InterviewBank(version=version, questions=tuple(questions), sha256=digest)


def interview_bank_sha256(payload: dict[str, Any] | None = None) -> str:
    if payload is None:
        document = json.loads(_BANK_PATH.read_text(encoding="utf-8"))
    else:
        document = payload
    canonical = json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def interview_question_ids() -> tuple[str, ...]:
    return tuple(item.question_id for item in load_interview_bank().questions)


def ios_interview_path(workspace_root: Path) -> Path | None:
    for relative in _IOS_BANK_CANDIDATES:
        path = workspace_root / relative
        if path.is_file():
            return path
    matches = sorted(workspace_root.joinpath("3_ios").glob("**/interview.json"))
    return matches[0] if matches else None


def ios_interview_question_ids(workspace_root: Path) -> tuple[str, ...] | None:
    path = ios_interview_path(workspace_root)
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        raw = payload.get("questions")
        items = raw if isinstance(raw, list) else []
    else:
        raise RuntimeError("iOS interview.json must be an object or array")
    ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question_id = item.get("question_id") or item.get("id")
        if isinstance(question_id, str) and question_id:
            ids.append(question_id)
    return tuple(ids)
