"""Match index writes. Does not store skill bodies or call a model."""

from __future__ import annotations

from app.services.match_index.index import MatchIndexService
from app.services.match_index.materials import SessionAskerSpirits, read_asker_materials
from app.services.match_index.owned import OwnedMatchIndex
from app.services.v3_placeholder import unavailable

__all__ = [
    "MatchIndexService",
    "OwnedMatchIndex",
    "SessionAskerSpirits",
    "read_asker_materials",
    "reject",
]


def reject() -> None:
    unavailable("match_index")
