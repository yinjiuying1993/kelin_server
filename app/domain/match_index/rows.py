"""Match-index slot changes. Questions only see an active title and keywords."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID


class MatchIndexError(Exception):
    def __init__(self) -> None:
        super().__init__("match index unavailable")


@dataclass(frozen=True)
class MatchRow:
    craft_id: UUID
    spirit_id: UUID
    skill_slug: str
    active_title: str | None
    active_keywords: tuple[str, ...] | None
    staged_title: str | None
    staged_keywords: tuple[str, ...] | None


@dataclass(frozen=True)
class QuestionMaterial:
    skill_slug: str
    title: str
    keywords: tuple[str, ...]


def stage(
    row: MatchRow | None,
    *,
    craft_id: UUID,
    spirit_id: UUID,
    skill_slug: str,
    title: str,
    keywords: tuple[str, ...],
) -> MatchRow:
    staged_title, staged_keywords = _pair(title, keywords)
    if row is None:
        return MatchRow(
            craft_id=craft_id,
            spirit_id=spirit_id,
            skill_slug=skill_slug,
            active_title=None,
            active_keywords=None,
            staged_title=staged_title,
            staged_keywords=staged_keywords,
        )
    return replace(row, staged_title=staged_title, staged_keywords=staged_keywords)


def activate(row: MatchRow | None) -> MatchRow:
    if row is None or row.staged_title is None or row.staged_keywords is None:
        raise MatchIndexError
    return replace(
        row,
        active_title=row.staged_title,
        active_keywords=row.staged_keywords,
        staged_title=None,
        staged_keywords=None,
    )


def withdraw(row: MatchRow | None) -> MatchRow | None:
    if row is None:
        return None
    return replace(row, staged_title=None, staged_keywords=None)


def remove_active(row: MatchRow | None) -> MatchRow | None:
    if row is None:
        return None
    return replace(row, active_title=None, active_keywords=None)


def question_materials(
    rows: list[MatchRow],
    *,
    exclude_spirit_id: UUID | None = None,
) -> list[QuestionMaterial]:
    visible: list[QuestionMaterial] = []
    for row in rows:
        if exclude_spirit_id is not None and row.spirit_id == exclude_spirit_id:
            continue
        if row.active_title is None or row.active_keywords is None:
            continue
        visible.append(
            QuestionMaterial(
                skill_slug=row.skill_slug,
                title=row.active_title,
                keywords=row.active_keywords,
            )
        )
    return visible


def _pair(title: str, keywords: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    cleaned_title = title.strip()
    cleaned = tuple(item.strip() for item in keywords if item.strip())
    if not cleaned_title or not cleaned:
        raise MatchIndexError
    return cleaned_title, cleaned
