"""Photo sight moderation rules. Spec §§8.4, 11.4, 16.2.

Unknown Vision props are rejected. Safety block writes no memory, growth, or prop.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final, Literal
from uuid import UUID

SIGHT_MODERATE_OPERATION = "sight.moderate"
SIGHT_PROP_KINDS: Final[frozenset[str]] = frozenset({"lamp", "plant", "book", "object", "other"})
SightPropKind = Literal["lamp", "plant", "book", "object", "other"]
DEFAULT_SIGHT_PROP: SightPropKind = "lamp"
DEFAULT_SIGHT_SUMMARY = "窗台上的见闻"
SAFETY_ALLOW = "allow"
SAFETY_BLOCK = "block"


def allowed_prop(value: str) -> SightPropKind | None:
    if value in SIGHT_PROP_KINDS:
        return value  # type: ignore[return-value]
    return None


def moderate_request_hash(*, feed_id: UUID, upload_session_id: UUID) -> str:
    canonical = json.dumps(
        {
            "feed_id": str(feed_id),
            "upload_session_id": str(upload_session_id),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()
