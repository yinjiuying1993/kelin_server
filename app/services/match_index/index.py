"""Four match-index writes. A question sees only the active slot."""

from __future__ import annotations

from uuid import UUID

from app.domain.match_index.rows import (
    QuestionMaterial,
    activate,
    question_materials,
    remove_active,
    stage,
    withdraw,
)
from app.repositories.match_index import MatchStore


class MatchIndexService:
    def __init__(self, store: MatchStore) -> None:
        self._store = store

    async def stage(
        self,
        *,
        craft_id: UUID,
        spirit_id: UUID,
        skill_slug: str,
        title: str,
        keywords: tuple[str, ...],
    ) -> None:
        current = await self._store.get(craft_id)
        await self._store.save(
            stage(
                current,
                craft_id=craft_id,
                spirit_id=spirit_id,
                skill_slug=skill_slug,
                title=title,
                keywords=keywords,
            )
        )

    async def activate(self, *, craft_id: UUID) -> None:
        await self._store.save(activate(await self._store.get(craft_id)))

    async def withdraw(self, *, craft_id: UUID) -> None:
        updated = withdraw(await self._store.get(craft_id))
        if updated is not None:
            await self._store.save(updated)

    async def remove_active(self, *, craft_id: UUID) -> None:
        updated = remove_active(await self._store.get(craft_id))
        if updated is not None:
            await self._store.save(updated)

    async def materials_for_questions(self) -> list[QuestionMaterial]:
        return question_materials(await self._store.list_rows())

    async def materials_for_asker(
        self, *, asker_spirit_id: UUID | None
    ) -> list[QuestionMaterial]:
        rows = await self._store.list_for_asker(asker_spirit_id)
        return question_materials(rows, exclude_spirit_id=asker_spirit_id)
