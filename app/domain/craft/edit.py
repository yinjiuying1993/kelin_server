"""Idempotency hashes for save, visibility, and delete. The slug stays on the server."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from app.domain.craft.skill_body import PreparedCraftContent

CRAFT_SKILL_SAVE_OPERATION = "craft.skill.save"
CRAFT_SKILL_VISIBILITY_OPERATION = "craft.skill.visibility"
CRAFT_SKILL_DELETE_OPERATION = "craft.skill.delete"


def _digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def craft_save_request_hash(craft_id: UUID, content: PreparedCraftContent) -> str:
    return _digest(
        {
            "craft_id": str(craft_id),
            "detail": content.detail,
            "keywords": list(content.keywords),
            "reference_links": list(content.reference_links),
            "title": content.title,
        }
    )


def craft_visibility_request_hash(craft_id: UUID, visibility: str) -> str:
    return _digest({"craft_id": str(craft_id), "visibility": visibility})


def craft_delete_request_hash(craft_id: UUID) -> str:
    return _digest({"craft_id": str(craft_id)})
