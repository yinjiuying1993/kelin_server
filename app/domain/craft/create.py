"""Idempotent craft create. The skill slug never leaves the server."""

from __future__ import annotations

import hashlib
import json

from app.domain.craft.skill_body import PreparedCraftContent

CRAFT_SKILL_CREATE_OPERATION = "craft.skill.create"


class SkillSlugTaken(Exception):
    def __init__(self) -> None:
        super().__init__("skill slug taken")


def craft_create_request_hash(content: PreparedCraftContent) -> str:
    payload = {
        "detail": content.detail,
        "keywords": list(content.keywords),
        "reference_links": list(content.reference_links),
        "title": content.title,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
