"""Open one ask dialogue from a confirmed match. History stays empty."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.repositories.ask import SkillSnapshot, SqlAskDialogue, StoredAsk
from app.schemas.craft import rank_for_points
from app.services.ask.match import MatchChoice


class AskDialogues(Protocol):
    async def find(self, asker_user_id: UUID, client_id: UUID) -> StoredAsk | None: ...

    async def insert(self, **fields: object) -> UUID: ...

    async def snapshot(self, skill_slug: str, spirit_id: UUID) -> SkillSnapshot | None: ...


class AskMatcher(Protocol):
    async def match(self, question: str) -> MatchChoice | None: ...


async def open_matched_dialogue(
    dialogues: AskDialogues,
    matcher: AskMatcher,
    *,
    asker_user_id: UUID,
    client_id: UUID,
    question: str,
) -> StoredAsk | None:
    existing = await dialogues.find(asker_user_id, client_id)
    if existing is not None:
        return existing
    choice = await matcher.match(question)
    if choice is None:
        return None
    copied = await dialogues.snapshot(choice.skill_slug, choice.spirit_id)
    if copied is None or not _same_material(copied, choice):
        return None
    if copied.spirit_owner_user_id == asker_user_id:
        return None
    question_id = await dialogues.insert(
        asker_user_id=asker_user_id,
        client_id=client_id,
        spirit_id=choice.spirit_id,
        spirit_owner_user_id=copied.spirit_owner_user_id,
        question_text=question,
        snapshot_title=copied.title,
        snapshot_rank_name=rank_for_points(copied.points).name,
        snapshot_skill_slug=choice.skill_slug,
        snapshot_body=copied.body,
        snapshot_detail=copied.detail,
        snapshot_reference_links=copied.reference_links,
    )
    return StoredAsk(
        question_id=question_id,
        question_text=question,
        snapshot_title=copied.title,
        snapshot_rank_name=rank_for_points(copied.points).name,
        snapshot_detail=copied.detail,
        snapshot_reference_links=copied.reference_links,
        snapshot_body=copied.body,
        first_reply_at=None,
    )


def _same_material(copied: SkillSnapshot, choice: MatchChoice) -> bool:
    return copied.title == choice.title and copied.keywords == choice.keywords


__all__ = ["AskDialogues", "AskMatcher", "SqlAskDialogue", "open_matched_dialogue"]
