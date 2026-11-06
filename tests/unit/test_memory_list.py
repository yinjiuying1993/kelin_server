from __future__ import annotations

from app.domain.memory_list import (
    LIST_DB_TYPES,
    db_memory_type,
    memory_types_for_filter,
    public_memory_type,
    require_public_memory_type,
)


def test_v1_filters_map_relationship_to_db() -> None:
    assert memory_types_for_filter("all") == LIST_DB_TYPES
    assert memory_types_for_filter("relationship") == ("relationship",)
    assert memory_types_for_filter("knowledge") == ("knowledge",)
    assert memory_types_for_filter("preference") == ("preference",)
    assert memory_types_for_filter("emotion") == ("emotion",)
    assert set(LIST_DB_TYPES) == {"preference", "knowledge", "emotion", "relationship"}


def test_public_and_db_memory_type_round_trip() -> None:
    assert public_memory_type("relation") is None
    assert public_memory_type("relationship") == "relationship"
    assert public_memory_type("speech") is None
    assert public_memory_type("sight") is None
    assert require_public_memory_type("emotion") == "emotion"
    assert db_memory_type("relationship") == "relationship"
    assert db_memory_type("preference") == "preference"
