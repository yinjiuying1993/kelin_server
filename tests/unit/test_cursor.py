from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from app.domain.cursor import (
    CURSOR_PREFIX,
    CURSOR_VERSION,
    MEMORIES_CURSOR_PREFIX,
    InvalidCursor,
    MessageCursor,
    decode_memory_cursor,
    decode_message_cursor,
    encode_memory_cursor,
    encode_message_cursor,
    memories_filter_hash,
    messages_filter_hash,
)

SECRET = b"unit-test-cursor-hmac"
OWNER = UUID("00000000-0000-4000-8000-0000000000aa")
OTHER = UUID("00000000-0000-4000-8000-0000000000bb")
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[2] / "app" / "repositories" / "messages.py"
SERVICE = Path(__file__).resolve().parents[2] / "app" / "services" / "messages.py"
ROUTER = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "messages.py"
MEMORY_REPO = Path(__file__).resolve().parents[2] / "app" / "repositories" / "memory.py"
MEMORY_SERVICE = Path(__file__).resolve().parents[2] / "app" / "services" / "memory.py"
MEMORY_ROUTER = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "memories.py"


def _cursor() -> MessageCursor:
    return MessageCursor(
        version=CURSOR_VERSION,
        sort_time=NOW - timedelta(seconds=3),
        id=UUID("00000000-0000-4000-8000-000000000001"),
        snapshot_at=NOW,
        filter_hash=messages_filter_hash(OWNER),
    )


def test_cursor_roundtrip() -> None:
    token = encode_message_cursor(_cursor(), secret=SECRET)
    assert token.startswith(CURSOR_PREFIX)
    assert not token.startswith("eyJ")
    decoded = decode_message_cursor(
        token, secret=SECRET, expected_filter_hash=messages_filter_hash(OWNER)
    )
    assert decoded.id == _cursor().id
    assert decoded.sort_time == _cursor().sort_time
    assert decoded.snapshot_at == _cursor().snapshot_at
    assert decoded.filter_hash == messages_filter_hash(OWNER)


def test_tampered_or_foreign_filter_or_bad_token_is_invalid() -> None:
    token = encode_message_cursor(_cursor(), secret=SECRET)
    flipped = token[:-1] + ("a" if token[-1] != "a" else "b")
    with pytest.raises(InvalidCursor):
        decode_message_cursor(
            flipped, secret=SECRET, expected_filter_hash=messages_filter_hash(OWNER)
        )
    with pytest.raises(InvalidCursor):
        decode_message_cursor(
            token, secret=SECRET, expected_filter_hash=messages_filter_hash(OTHER)
        )
    with pytest.raises(InvalidCursor):
        decode_message_cursor(
            "not-a-cursor", secret=SECRET, expected_filter_hash=messages_filter_hash(OWNER)
        )
    with pytest.raises(InvalidCursor):
        decode_message_cursor(
            "kelin.msg.v1.a", secret=SECRET, expected_filter_hash=messages_filter_hash(OWNER)
        )
    with pytest.raises(InvalidCursor):
        decode_message_cursor(
            token, secret=b"other-secret", expected_filter_hash=messages_filter_hash(OWNER)
        )


def test_sort_time_after_snapshot_is_invalid() -> None:
    token = encode_message_cursor(
        MessageCursor(
            version=CURSOR_VERSION,
            sort_time=NOW + timedelta(seconds=1),
            id=UUID("00000000-0000-4000-8000-000000000001"),
            snapshot_at=NOW,
            filter_hash=messages_filter_hash(OWNER),
        ),
        secret=SECRET,
    )
    with pytest.raises(InvalidCursor):
        decode_message_cursor(
            token, secret=SECRET, expected_filter_hash=messages_filter_hash(OWNER)
        )


def _source_without_docs(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    while '"""' in text:
        start = text.find('"""')
        end = text.find('"""', start + 3)
        if end < 0:
            break
        text = text[:start] + text[end + 3 :]
    return text


def test_pagination_code_does_not_use_offset() -> None:
    for path in (REPO, SERVICE, ROUTER, MEMORY_REPO, MEMORY_SERVICE, MEMORY_ROUTER):
        source = _source_without_docs(path)
        assert "OFFSET" not in source.upper()
    repo = REPO.read_text(encoding="utf-8")
    assert "ORDER BY m.created_at DESC, m.id DESC" in repo
    assert "LIMIT :fetch_limit" in repo
    assert "role <> 'system'" in repo
    router = ROUTER.read_text(encoding="utf-8")
    assert "claimed_read_transaction" in router
    assert "load_message_page" in router
    assert "SELECT" not in router
    memory_repo = MEMORY_REPO.read_text(encoding="utf-8")
    assert "ORDER BY m.created_at DESC, m.id DESC" in memory_repo
    assert "LIMIT :fetch_limit" in memory_repo
    assert "status = 'active'" in memory_repo
    memory_router = MEMORY_ROUTER.read_text(encoding="utf-8")
    assert "claimed_read_transaction" in memory_router
    assert "load_memory_page" in memory_router
    assert "SELECT" not in memory_router


def test_memory_cursor_binds_filter_and_rejects_message_prefix() -> None:
    all_hash = memories_filter_hash(OWNER, "all")
    knowledge_hash = memories_filter_hash(OWNER, "knowledge")
    assert all_hash != knowledge_hash
    assert all_hash != memories_filter_hash(OTHER, "all")
    cursor = MessageCursor(
        version=CURSOR_VERSION,
        sort_time=NOW - timedelta(seconds=3),
        id=UUID("00000000-0000-4000-8000-000000000001"),
        snapshot_at=NOW,
        filter_hash=all_hash,
    )
    token = encode_memory_cursor(cursor, secret=SECRET)
    assert token.startswith(MEMORIES_CURSOR_PREFIX)
    decoded = decode_memory_cursor(token, secret=SECRET, expected_filter_hash=all_hash)
    assert decoded.filter_hash == all_hash
    with pytest.raises(InvalidCursor):
        decode_memory_cursor(token, secret=SECRET, expected_filter_hash=knowledge_hash)
    message_token = encode_message_cursor(
        MessageCursor(
            version=CURSOR_VERSION,
            sort_time=NOW - timedelta(seconds=3),
            id=UUID("00000000-0000-4000-8000-000000000001"),
            snapshot_at=NOW,
            filter_hash=messages_filter_hash(OWNER),
        ),
        secret=SECRET,
    )
    with pytest.raises(InvalidCursor):
        decode_memory_cursor(message_token, secret=SECRET, expected_filter_hash=all_hash)
