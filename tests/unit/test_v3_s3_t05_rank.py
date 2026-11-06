"""Rank names follow the point bands. No explanation sentence is stored."""

from __future__ import annotations

import pytest
from app.schemas.craft import CraftSkillSummary, RankProgress, rank_for_points

_BANDS = (
    (0, None, 1, False),
    (1, "门边学徒", 99, False),
    (99, "门边学徒", 1, False),
    (100, "搭手帮工", 200, False),
    (299, "搭手帮工", 1, False),
    (300, "上手匠人", 300, False),
    (599, "上手匠人", 1, False),
    (600, "独当师傅", 400, False),
    (999, "独当师傅", 1, False),
    (1000, "掌作名匠", 500, False),
    (1499, "掌作名匠", 1, False),
    (1500, "镇场宗师", None, True),
    (2000, "镇场宗师", None, True),
    (2001, "镇场宗师", None, True),
)


@pytest.mark.parametrize(("points", "name", "points_to_next", "at_max_rank"), _BANDS)
def test_rank_boundaries(
    points: int,
    name: str | None,
    points_to_next: int | None,
    at_max_rank: bool,
) -> None:
    progress = rank_for_points(points)
    assert progress.name == name
    assert progress.points_to_next == points_to_next
    assert progress.at_max_rank is at_max_rank


def test_rank_names_are_four_characters_and_carry_no_explanation() -> None:
    assert set(RankProgress.__dataclass_fields__) == {"name", "points_to_next", "at_max_rank"}
    assert "explanation" not in CraftSkillSummary.model_fields
    names = {name for _, name, _, _ in _BANDS if name is not None}
    assert names == {"门边学徒", "搭手帮工", "上手匠人", "独当师傅", "掌作名匠", "镇场宗师"}
    assert all(len(name) == 4 for name in names)
