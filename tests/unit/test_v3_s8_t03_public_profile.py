"""V3-S8-T03 the public profile keeps only name, public titles, ranks, and help count."""

from __future__ import annotations

import inspect
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.repositories import spirit_public as profile_sql
from app.repositories.spirit_public import PublicProfileRow, PublicSkillRow
from app.services.craft_ledger.public_profile import read_public_profile

_SPIRIT = UUID("00000000-0000-4000-8000-0000000000a8")
_MISSING = UUID("00000000-0000-4000-8000-0000000000a9")


class _Profiles:
    def __init__(self, rows: dict[UUID, PublicProfileRow | None]) -> None:
        self.rows = rows

    async def read(self, spirit_id: UUID) -> PublicProfileRow | None:
        return self.rows.get(spirit_id)


@pytest.mark.asyncio
async def test_public_titles_and_current_ranks_only() -> None:
    shown = await read_public_profile(
        _Profiles(
            {
                _SPIRIT: PublicProfileRow(
                    _SPIRIT,
                    "雾生",
                    2,
                    (
                        PublicSkillRow("倒茶", "掌作名匠"),
                        PublicSkillRow("烧水", None),
                    ),
                )
            }
        ),
        spirit_id=_SPIRIT,
    )
    assert set(shown.model_dump()) == {"id", "name", "help_count", "skills"}
    assert shown.name == "雾生"
    assert shown.help_count == 2
    assert [(skill.title, skill.rank_name) for skill in shown.skills] == [
        ("倒茶", "掌作名匠"),
        ("烧水", None),
    ]
    for skill in shown.skills:
        assert set(skill.model_dump()) == {"title", "rank_name"}
    dumped = shown.model_dump_json()
    for hidden in ("skill_slug", "keywords", "points", "body", "question"):
        assert hidden not in dumped


@pytest.mark.asyncio
async def test_no_public_skill_still_returns_the_spirit() -> None:
    shown = await read_public_profile(
        _Profiles({_SPIRIT: PublicProfileRow(_SPIRIT, "雾生", 0, ())}),
        spirit_id=_SPIRIT,
    )
    assert shown.skills == []
    assert shown.help_count == 0
    assert shown.name == "雾生"


@pytest.mark.asyncio
async def test_missing_spirit_is_not_found() -> None:
    with pytest.raises(ApiError) as caught:
        await read_public_profile(_Profiles({}), spirit_id=_MISSING)
    assert caught.value.code == "NOT_FOUND"
    assert caught.value.message == "not found"
    assert caught.value.status_code == 404


def test_query_does_not_dump_the_skill_row() -> None:
    source = inspect.getsource(profile_sql)
    assert "visibility = 'public'" in source
    assert "deleted_at IS NULL" in source
    assert "craft_ledgers" in source
    assert "help_count" in source
    for forbidden in (
        "skill_slug",
        "keywords",
        "detail",
        "reference_links",
        "body",
        "points",
        "points_to_next",
        "total_points",
        "draft_",
        "ask_questions",
        "question_text",
        "snapshot_",
        "SELECT *",
        "next_cursor",
    ):
        assert forbidden not in source
