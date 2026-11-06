"""Skill id and markdown body. V3-S3-T01.

The slug is stored on the server. Rejection messages do not include it.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import Literal

MAX_BODY_LINES = 500
MAX_DESCRIPTION_CHARS = 1024
_SLUG_ATTEMPTS = 8
_SLUG = re.compile(r"^[a-z0-9-]{1,64}$")
_THIN_DETAIL = re.compile(r"(?:帮忙|处理好|[\s，。,.、；;：:！!？?]+)+")
ModerationDecision = Literal["allow", "block"]


class CraftContentRejected(ValueError):
    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(field)


class CraftModerationRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("moderation rejected")


class CraftSlugExhausted(ValueError):
    def __init__(self) -> None:
        super().__init__("skill slug collision")


@dataclass(frozen=True)
class PreparedCraftContent:
    title: str
    keywords: tuple[str, ...]
    detail: str
    reference_links: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class CraftDraft:
    skill_slug: str
    title: str
    keywords: tuple[str, ...]
    detail: str
    reference_links: tuple[str, ...]
    description: str
    body: str


def new_skill_slug(
    taken: Collection[str],
    rng: Callable[[], str] | None = None,
) -> str:
    """Return a slug not in this spirit's set, including deleted skills."""
    choose = rng or _default_slug
    used = set(taken)
    for _ in range(_SLUG_ATTEMPTS):
        slug = choose()
        if slug in used or _SLUG.fullmatch(slug) is None:
            continue
        return slug
    raise CraftSlugExhausted


def prepare_craft_content(
    *,
    title: str,
    keywords: str | Sequence[str],
    detail: str,
    reference_links: str | Sequence[str] = (),
    moderation: ModerationDecision,
) -> PreparedCraftContent:
    """Validate and reject illegal content before a skill id is issued."""
    title_text = _clean_title(title)
    keyword_lines = _keyword_lines(keywords, title_text)
    detail_text = _clean_detail(detail)
    link_lines = _link_lines(reference_links)
    description = _description(title_text, keyword_lines)
    _reject_if_too_long(keyword_lines, detail_text, link_lines, title_text, description)
    _refuse_moderation(moderation)
    return PreparedCraftContent(
        title=title_text,
        keywords=keyword_lines,
        detail=detail_text,
        reference_links=link_lines,
        description=description,
    )


def render_skill_body(content: PreparedCraftContent, skill_slug: str) -> str:
    if _SLUG.fullmatch(skill_slug) is None:
        raise ValueError("invalid skill slug")
    return _render(
        skill_slug,
        content.title,
        content.description,
        content.keywords,
        content.detail,
        content.reference_links,
    )


def compose_skill(
    *,
    title: str,
    keywords: str | Sequence[str],
    detail: str,
    reference_links: str | Sequence[str] = (),
    taken: Collection[str],
    moderation: ModerationDecision,
    rng: Callable[[], str] | None = None,
) -> CraftDraft:
    content = prepare_craft_content(
        title=title,
        keywords=keywords,
        detail=detail,
        reference_links=reference_links,
        moderation=moderation,
    )
    skill_slug = new_skill_slug(taken, rng)
    return CraftDraft(
        skill_slug=skill_slug,
        title=content.title,
        keywords=content.keywords,
        detail=content.detail,
        reference_links=content.reference_links,
        description=content.description,
        body=render_skill_body(content, skill_slug),
    )


def _default_slug() -> str:
    return secrets.token_hex(16)


def _clean_title(value: str) -> str:
    if "\n" in value or "\r" in value or not value.strip():
        raise CraftContentRejected("title")
    return value.strip()


def _keyword_lines(value: str | Sequence[str], title: str) -> tuple[str, ...]:
    lines = _split_lines(value)
    if not lines or all(line == title for line in lines):
        raise CraftContentRejected("keywords")
    return lines


def _clean_detail(value: str) -> str:
    stripped = value.strip()
    if not stripped or _THIN_DETAIL.fullmatch(stripped):
        raise CraftContentRejected("detail")
    return stripped


def _link_lines(value: str | Sequence[str]) -> tuple[str, ...]:
    return _split_lines(value)


def _split_lines(value: str | Sequence[str]) -> tuple[str, ...]:
    raw = value.splitlines() if isinstance(value, str) else value
    lines: list[str] = []
    for item in raw:
        lines.extend(part.strip() for part in str(item).splitlines())
    return tuple(line for line in lines if line)


def _description(title: str, keywords: Sequence[str]) -> str:
    joined = "、".join(keywords)
    text = f"{title}。遇到{joined}相关的问题时使用。"
    if len(text) > MAX_DESCRIPTION_CHARS:
        field = "title" if len(title) >= len(joined) else "keywords"
        raise CraftContentRejected(field)
    return text


def _reject_if_too_long(
    keywords: Sequence[str],
    detail: str,
    links: Sequence[str],
    title: str,
    description: str,
) -> None:
    lines = _render("x", title, description, keywords, detail, links).splitlines()
    if len(lines) <= MAX_BODY_LINES:
        return
    detail_lines = len(detail.splitlines())
    ranked = (
        (detail_lines, "detail"),
        (len(keywords), "keywords"),
        (len(links), "reference_links"),
    )
    raise CraftContentRejected(max(ranked)[1])


def _refuse_moderation(decision: str) -> None:
    if decision == "block":
        raise CraftModerationRejected
    if decision != "allow":
        raise ValueError("unknown moderation decision")


def _render(
    skill_slug: str,
    title: str,
    description: str,
    keywords: Sequence[str],
    detail: str,
    links: Sequence[str],
) -> str:
    lines = [
        "---",
        f"name: {skill_slug}",
        f"description: {description}",
        "---",
        "",
        f"# {title}",
        "",
        "## 关键词",
        *keywords,
        "",
        "## 详细描述",
        *detail.splitlines(),
    ]
    if links:
        lines.extend(["", "## 参考链接", *links])
    return "\n".join(lines)
