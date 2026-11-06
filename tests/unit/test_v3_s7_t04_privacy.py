"""V3-S7-T04 the other account cannot read the question or an unpublished skill."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.db.rls_matrix import RLS_MATRIX_V3
from app.repositories.ask_history import HistoryRow
from app.repositories.craft import OwnerSkillDetail
from app.services.ask.history import list_recent_questions, read_answered_question
from app.services.craft.owner_list import get_owner_skill

_ASKER = UUID("00000000-0000-4000-8000-0000000000d1")
_OWNER = UUID("00000000-0000-4000-8000-0000000000d2")
_ASKER_SPIRIT = UUID("00000000-0000-4000-8000-0000000000d3")
_OWNER_SPIRIT = UUID("00000000-0000-4000-8000-0000000000d4")
_QUESTION = UUID("00000000-0000-4000-8000-0000000000d5")
_CRAFT = UUID("00000000-0000-4000-8000-0000000000d6")
_MISSING = UUID("00000000-0000-4000-8000-0000000000d7")
_SECRET = "谁把方子抄走了"
_UNPUBLISHED = "未公开的方子"
_AT = datetime(2026, 11, 4, 4, tzinfo=UTC)


class _Questions:
    def __init__(self) -> None:
        self.row = HistoryRow(_QUESTION, _SECRET, "雾生", "门边学徒", _AT, None)

    async def list_history(
        self,
        *,
        asker_user_id: UUID,
        view: str,
        snapshot_at: datetime,
        fetch_limit: int,
        cursor_time: datetime | None,
        cursor_id: UUID | None,
    ) -> list[HistoryRow]:
        del view, snapshot_at, fetch_limit, cursor_time, cursor_id
        if asker_user_id != _ASKER:
            return []
        return [self.row]

    async def read_history(
        self, asker_user_id: UUID, question_id: UUID
    ) -> tuple[HistoryRow, tuple[tuple[str, str], ...]] | None:
        if asker_user_id != _ASKER or question_id != _QUESTION:
            return None
        return self.row, (("user", _SECRET), ("spirit", "先烧水。"))


class _Skills:
    async def owner_spirit(self, owner_id: UUID) -> UUID | None:
        if owner_id == _OWNER:
            return _OWNER_SPIRIT
        if owner_id == _ASKER:
            return _ASKER_SPIRIT
        return None

    async def get_skill(self, craft_id: UUID, spirit_id: UUID) -> OwnerSkillDetail | None:
        if craft_id != _CRAFT or spirit_id != _OWNER_SPIRIT:
            return None
        return OwnerSkillDetail(
            craft_id=_CRAFT,
            title="倒茶",
            visibility="unpublished",
            points=0,
            updated_at=_AT,
            keywords=("待客",),
            detail=_UNPUBLISHED,
            reference_links=("https://tea.example/secret",),
        )


class _Unit:
    def __init__(self) -> None:
        self._skills = _Skills()

    @asynccontextmanager
    async def transaction(self):
        yield self._skills


def _same_missing(left: ApiError, right: ApiError) -> None:
    assert left.code == right.code == "NOT_FOUND"
    assert left.message == right.message == "not found"
    assert left.status_code == right.status_code == 404
    assert left.retryable is right.retryable is False


@pytest.mark.asyncio
async def test_owner_cannot_read_the_askers_question() -> None:
    questions = _Questions()
    seen = await read_answered_question(questions, asker_user_id=_ASKER, question_id=_QUESTION)
    assert seen.question == _SECRET
    recent = await list_recent_questions(questions, asker_user_id=_OWNER, now=_AT)
    assert recent.items == []
    assert _SECRET not in recent.model_dump_json()
    with pytest.raises(ApiError) as hidden:
        await read_answered_question(questions, asker_user_id=_OWNER, question_id=_QUESTION)
    with pytest.raises(ApiError) as missing:
        await read_answered_question(questions, asker_user_id=_ASKER, question_id=_MISSING)
    _same_missing(hidden.value, missing.value)
    assert _SECRET not in hidden.value.message


@pytest.mark.asyncio
async def test_asker_cannot_read_an_unpublished_skill() -> None:
    unit = _Unit()
    owned = await get_owner_skill(unit, owner_id=_OWNER, craft_id=_CRAFT)
    assert owned.description == _UNPUBLISHED
    with pytest.raises(ApiError) as hidden:
        await get_owner_skill(unit, owner_id=_ASKER, craft_id=_CRAFT)
    with pytest.raises(ApiError) as missing:
        await get_owner_skill(unit, owner_id=_OWNER, craft_id=_MISSING)
    _same_missing(hidden.value, missing.value)
    assert _UNPUBLISHED not in hidden.value.message
    assert "https://tea.example/secret" not in hidden.value.message


def test_cross_account_select_is_empty_for_both_tables() -> None:
    rows = {row.table: row for row in RLS_MATRIX_V3}
    question = rows["ask_questions"]
    skill = rows["craft_skills"]
    asker_hidden = next(case for case in question.test_cases if case.name == "B_select_A")
    skill_hidden = next(case for case in skill.test_cases if case.name == "B_select_A")
    assert asker_hidden.actor == "B"
    assert asker_hidden.expected == "empty"
    assert skill_hidden.actor == "B"
    assert skill_hidden.expected == "empty"
    assert question.owner_sql == "asker_user_id = auth.uid()"
    assert "spirit_owner_user_id" not in question.owner_sql
    assert skill.owner_sql == (
        "EXISTS (SELECT 1 FROM public.spirits s WHERE s.id = spirit_id AND s.user_id = auth.uid())"
    )
