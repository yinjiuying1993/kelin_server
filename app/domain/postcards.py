"""Postcard list and read helpers. Spec §§14.4–14.5."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

POSTCARD_READ_OPERATION = "postcard.read"
POSTCARD_READ_EVENT = "postcard.read"


def postcard_read_hash(*, postcard_id: UUID) -> str:
    canonical = json.dumps(
        {"postcard_id": str(postcard_id)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
