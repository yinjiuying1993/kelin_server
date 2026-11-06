"""Match payload and tie break. The model only sees the question and skill fields."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.match_index.rows import QuestionMaterial
from app.schemas.craft import rank_for_points

MATCH_REQUEST_CHAR_LIMIT = 24_000
_PROMPT_KEYS = frozenset({"question", "skills"})
_SKILL_KEYS = frozenset({"skill_id", "title", "keywords"})
_RANK_WEIGHT = {
    None: 0,
    "门边学徒": 1,
    "搭手帮工": 2,
    "上手匠人": 3,
    "独当师傅": 4,
    "掌作名匠": 5,
    "镇场宗师": 6,
}


@dataclass(frozen=True)
class ConfirmableSkill:
    skill_slug: str
    spirit_id: UUID
    title: str
    keywords: tuple[str, ...]
    public: bool
    points: int
    help_count: int
    updated_at: datetime


def match_payload(question: str, materials: list[QuestionMaterial]) -> dict[str, object]:
    return {
        "question": question,
        "skills": [
            {
                "skill_id": item.skill_slug,
                "title": item.title,
                "keywords": list(item.keywords),
            }
            for item in materials
        ],
    }


def payload_fits(payload: dict[str, object], *, limit: int = MATCH_REQUEST_CHAR_LIMIT) -> bool:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return len(encoded) <= limit


def payload_fields_are_allowed(payload: dict[str, object]) -> bool:
    if set(payload) != _PROMPT_KEYS:
        return False
    skills = payload["skills"]
    if not isinstance(skills, list):
        return False
    return all(isinstance(item, dict) and set(item) == _SKILL_KEYS for item in skills)


def choose_skill(rows: list[ConfirmableSkill]) -> ConfirmableSkill | None:
    if not rows:
        return None
    return max(rows, key=_sort_key)


def _sort_key(row: ConfirmableSkill) -> tuple[int, int, int, datetime]:
    return (
        _RANK_WEIGHT[rank_for_points(row.points).name],
        row.points,
        row.help_count,
        row.updated_at,
    )
