from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.domain.memory_recall import memory_ids_of, visible_source_refs
from app.schemas.jsonb import SourceRef

REPO = Path(__file__).resolve().parents[2] / "app" / "repositories" / "memory_recall.py"
CHAT_REPO = Path(__file__).resolve().parents[2] / "app" / "repositories" / "chat.py"
BAILIAN = Path(__file__).resolve().parents[2] / "app" / "integrations" / "bailian.py"
CHAT_SERVICE = Path(__file__).resolve().parents[2] / "app" / "services" / "chat.py"


def test_visible_source_refs_drop_inactive_memories_and_keep_web() -> None:
    active = uuid4()
    sealed = uuid4()
    refs = (
        SourceRef(type="memory", id=active),
        SourceRef(type="memory", id=sealed),
        SourceRef(
            type="web",
            url="https://example.com/a",
            fetched_at=datetime(2026, 9, 10, 4, 0, tzinfo=UTC),
        ),
    )
    visible = visible_source_refs(refs, active_memory_ids=frozenset({active}))
    assert [ref.type for ref in visible] == ["memory", "web"]
    assert visible[0].id == active
    assert memory_ids_of(refs) == (active, sealed)
    assert memory_ids_of(visible) == (active,)


def test_recall_sql_requires_active_and_owner_and_avoids_offset() -> None:
    source = REPO.read_text(encoding="utf-8")
    assert source.count("status = 'active'") >= 4
    assert "s.user_id = :owner_id" in source
    assert "OFFSET" not in source.upper()
    assert "FROM public.memories" in source
    assert "FROM public.style_samples" in source
    chat_repo = CHAT_REPO.read_text(encoding="utf-8")
    assert "fetch_active_owned_memory_ids" not in chat_repo
    assert "FROM public.memories" not in chat_repo


def test_search_and_prompt_adapter_boundaries_do_not_select_invalid_memories() -> None:
    bailian = BAILIAN.read_text(encoding="utf-8")
    chat = CHAT_SERVICE.read_text(encoding="utf-8")
    assert "FROM public.memories" not in bailian
    assert "load_prompt_recall" in chat
    assert "_display_source_refs" in chat
    assert "SearchInput" in chat
    assert "FROM public.memories" not in chat
