"""Skill id collision, body length, and the moderation rejection entry."""

from __future__ import annotations

import pytest
from app.domain.craft.skill_body import (
    MAX_DESCRIPTION_CHARS,
    CraftContentRejected,
    CraftModerationRejected,
    CraftSlugExhausted,
    compose_skill,
    new_skill_slug,
)


def test_collision_retries_without_reusing_a_deleted_slug() -> None:
    pending = iter(("old-skill", "old-skill", "fresh-skill"))
    slug = new_skill_slug({"old-skill"}, lambda: next(pending))
    assert slug == "fresh-skill"
    assert slug not in {"old-skill"}


def test_exhausted_collision_hides_the_slug() -> None:
    with pytest.raises(CraftSlugExhausted) as caught:
        new_skill_slug({"taken-id"}, lambda: "taken-id")
    assert "taken-id" not in str(caught.value)


def test_uppercase_candidate_is_skipped() -> None:
    pending = iter(("Pour-Tea", "pour-tea"))
    assert new_skill_slug(set(), lambda: next(pending)) == "pour-tea"


def test_body_over_500_lines_rejects_before_a_slug_is_issued() -> None:
    def fail_if_called() -> str:
        raise AssertionError("slug allocated")

    sample = compose_skill(
        title="泡茶",
        keywords=["水温"],
        detail="先烧水。",
        taken=set(),
        moderation="allow",
        rng=lambda: "sample-skill",
    )
    extra = 500 - (len(sample.body.splitlines()) - 1)
    too_long = "\n".join(["先烧水"] * (extra + 1))
    with pytest.raises(CraftContentRejected) as caught:
        compose_skill(
            title="泡茶",
            keywords=["水温"],
            detail=too_long,
            taken=set(),
            moderation="allow",
            rng=fail_if_called,
        )
    assert caught.value.field == "detail"
    assert "sample-skill" not in str(caught.value)


def test_moderation_block_rejects_before_a_slug_is_issued() -> None:
    def fail_if_called() -> str:
        raise AssertionError("slug allocated")

    with pytest.raises(CraftModerationRejected) as caught:
        compose_skill(
            title="泡茶",
            keywords=["水温"],
            detail="先烧水，再闷三分钟。",
            taken=set(),
            moderation="block",
            rng=fail_if_called,
        )
    assert "skill" not in str(caught.value)


def test_body_follows_the_markdown_template() -> None:
    draft = compose_skill(
        title="泡茶",
        keywords=["水温", "闷泡"],
        detail="先烧水。",
        reference_links=["https://example.com/tea"],
        taken=set(),
        moderation="allow",
        rng=lambda: "pour-tea",
    )
    assert draft.skill_slug == "pour-tea"
    assert draft.description == "泡茶。遇到水温、闷泡相关的问题时使用。"
    assert draft.skill_slug not in draft.description
    assert draft.body == "\n".join(
        [
            "---",
            "name: pour-tea",
            "description: 泡茶。遇到水温、闷泡相关的问题时使用。",
            "---",
            "",
            "# 泡茶",
            "",
            "## 关键词",
            "水温",
            "闷泡",
            "",
            "## 详细描述",
            "先烧水。",
            "",
            "## 参考链接",
            "https://example.com/tea",
        ]
    )
    without_links = compose_skill(
        title="泡茶",
        keywords=["水温"],
        detail="先烧水。",
        taken=set(),
        moderation="allow",
        rng=lambda: "pour-tea",
    )
    assert "## 参考链接" not in without_links.body


def test_keywords_that_only_repeat_the_title_are_rejected() -> None:
    with pytest.raises(CraftContentRejected) as caught:
        compose_skill(
            title="泡茶",
            keywords=["泡茶", " 泡茶 "],
            detail="先烧水。",
            taken=set(),
            moderation="allow",
        )
    assert caught.value.field == "keywords"


def test_thin_detail_is_rejected() -> None:
    for detail in ("帮忙", "处理好", "  帮忙。处理好  "):
        with pytest.raises(CraftContentRejected) as caught:
            compose_skill(
                title="泡茶",
                keywords=["水温"],
                detail=detail,
                taken=set(),
                moderation="allow",
            )
        assert caught.value.field == "detail"


def test_description_over_1024_is_rejected() -> None:
    overhead = len("茶。遇到相关的问题时使用。")
    fitting = "词" * (MAX_DESCRIPTION_CHARS - overhead)
    allowed = compose_skill(
        title="茶",
        keywords=[fitting],
        detail="先烧水。",
        taken=set(),
        moderation="allow",
        rng=lambda: "short-skill",
    )
    assert len(allowed.description) == MAX_DESCRIPTION_CHARS
    with pytest.raises(CraftContentRejected) as caught:
        compose_skill(
            title="茶",
            keywords=[fitting + "词"],
            detail="先烧水。",
            taken=set(),
            moderation="allow",
        )
    assert caught.value.field == "keywords"
