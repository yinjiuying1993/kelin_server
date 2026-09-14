"""Undirected friend-edge helpers. Spec §§8.9, 14.1–14.3."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

FRIEND_ADD_OPERATION = "friend.add"
FRIEND_REMOVE_OPERATION = "friend.remove"
FRIEND_ADDED_EVENT = "friend.added"
FRIEND_REMOVED_EVENT = "friend.removed"


def ordered_spirit_pair(left: UUID, right: UUID) -> tuple[UUID, UUID]:
    if left == right:
        raise ValueError("friend edge requires two distinct spirits")
    if left < right:
        return left, right
    return right, left


def friend_add_hash(*, invite_code: str) -> str:
    canonical = json.dumps(
        {"invite_code": invite_code},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def friend_remove_hash(*, friend_id: UUID) -> str:
    canonical = json.dumps(
        {"friend_id": str(friend_id)},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
