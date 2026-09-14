from __future__ import annotations

from app.domain.memory_list import RELATIONSHIP_TYPES, memory_types_for_filter


def test_relationship_maps_preference_relation_emotion() -> None:
    assert memory_types_for_filter("all") is None
    assert memory_types_for_filter("relationship") == RELATIONSHIP_TYPES
    assert memory_types_for_filter("knowledge") == ("knowledge",)
    assert memory_types_for_filter("speech") == ("speech",)
    assert memory_types_for_filter("sight") == ("sight",)
    assert set(RELATIONSHIP_TYPES) == {"preference", "relation", "emotion"}
