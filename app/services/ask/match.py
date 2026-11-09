"""One index read and one match-model call. No answer call and no dialogue write."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.domain.ask.match import (
    ConfirmableSkill,
    choose_skill,
    match_payload,
    payload_fits,
)
from app.domain.match_index.rows import QuestionMaterial


class MatchCallFailed(Exception):
    def __init__(self) -> None:
        super().__init__("match model unavailable")


@dataclass(frozen=True)
class MatchChoice:
    skill_slug: str
    spirit_id: UUID
    title: str
    keywords: tuple[str, ...]


class MaterialSource(Protocol):
    async def load(self) -> list[QuestionMaterial]: ...


class SkillConfirm(Protocol):
    async def confirm(self, skill_slugs: tuple[str, ...]) -> list[ConfirmableSkill]: ...


class MatchModel(Protocol):
    async def think(self, payload: dict[str, object]) -> list[str]: ...


async def match_published_skill(
    materials: MaterialSource,
    confirmer: SkillConfirm,
    model: MatchModel,
    *,
    asker_spirit_id: UUID | None,
    question: str,
    request_limit: int | None = None,
) -> MatchChoice | None:
    loaded = await materials.load()
    if not loaded:
        return None
    payload = match_payload(question, loaded)
    limit = {} if request_limit is None else {"limit": request_limit}
    if not payload_fits(payload, **limit):
        return None
    returned = await model.think(payload)
    by_slug = {item.skill_slug: item for item in loaded}
    ordered = _known_ids(returned, by_slug)
    if not ordered:
        return None
    confirmed = await confirmer.confirm(tuple(ordered))
    found = {row.skill_slug: row for row in confirmed}
    accepted = [
        found[slug]
        for slug in ordered
        if slug in found and _still_matches(found[slug], by_slug[slug], asker_spirit_id)
    ]
    chosen = choose_skill(accepted)
    if chosen is None:
        return None
    sent = by_slug[chosen.skill_slug]
    return MatchChoice(
        skill_slug=chosen.skill_slug,
        spirit_id=chosen.spirit_id,
        title=sent.title,
        keywords=sent.keywords,
    )


def _known_ids(returned: list[str], by_slug: dict[str, QuestionMaterial]) -> list[str]:
    ordered: list[str] = []
    for raw in returned:
        slug = raw.strip()
        if slug in by_slug and slug not in ordered:
            ordered.append(slug)
    return ordered


def _still_matches(
    row: ConfirmableSkill,
    sent: QuestionMaterial,
    asker_spirit_id: UUID | None,
) -> bool:
    if not row.public or row.spirit_id == asker_spirit_id:
        return False
    return row.title == sent.title and row.keywords == sent.keywords
