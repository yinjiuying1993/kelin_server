from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from app.domain.cursor import (
    CURSOR_VERSION,
    POSTCARDS_CURSOR_PREFIX,
    InvalidCursor,
    MessageCursor,
    decode_postcard_cursor,
    encode_postcard_cursor,
    friends_filter_hash,
    postcards_filter_hash,
)
from app.domain.postcards import postcard_read_hash

SECRET = b"unit-test-cursor-hmac"
OWNER = UUID("00000000-0000-4000-8000-0000000000aa")
OTHER = UUID("00000000-0000-4000-8000-0000000000bb")
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "app" / "db" / "migrations" / "versions" / "20260908_0013_postcard_definer.py"
REPO = ROOT / "app" / "repositories" / "postcards.py"
SERVICE = ROOT / "app" / "services" / "postcards.py"
ROUTER = ROOT / "app" / "api" / "v1" / "social.py"


def _source_without_docs(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    while '"""' in text:
        start = text.find('"""')
        end = text.find('"""', start + 3)
        if end < 0:
            break
        text = text[:start] + text[end + 3 :]
    return text


def test_postcard_cursor_binds_unread_filter() -> None:
    all_hash = postcards_filter_hash(OWNER, unread_only=False)
    unread_hash = postcards_filter_hash(OWNER, unread_only=True)
    assert all_hash != unread_hash
    assert all_hash != postcards_filter_hash(OTHER, unread_only=False)
    cursor = MessageCursor(
        version=CURSOR_VERSION,
        sort_time=NOW - timedelta(seconds=3),
        id=UUID("00000000-0000-4000-8000-000000000001"),
        snapshot_at=NOW,
        filter_hash=all_hash,
    )
    token = encode_postcard_cursor(cursor, secret=SECRET)
    assert token.startswith(POSTCARDS_CURSOR_PREFIX)
    decoded = decode_postcard_cursor(token, secret=SECRET, expected_filter_hash=all_hash)
    assert decoded.id == cursor.id
    with pytest.raises(InvalidCursor):
        decode_postcard_cursor(token, secret=SECRET, expected_filter_hash=unread_hash)
    with pytest.raises(InvalidCursor):
        decode_postcard_cursor(
            token, secret=SECRET, expected_filter_hash=friends_filter_hash(OWNER)
        )


def test_postcard_read_hash_is_stable() -> None:
    postcard_id = UUID("00000000-0000-4000-8000-0000000000e1")
    assert postcard_read_hash(postcard_id=postcard_id) == postcard_read_hash(
        postcard_id=postcard_id
    )
    assert postcard_read_hash(postcard_id=postcard_id) != postcard_read_hash(
        postcard_id=UUID("00000000-0000-4000-8000-0000000000e2")
    )


def test_postcard_sql_is_receiver_keyset_without_offset() -> None:
    for path in (REPO, SERVICE, ROUTER):
        source = _source_without_docs(path)
        assert "OFFSET" not in source.upper()
    repo = REPO.read_text(encoding="utf-8")
    assert "private.list_postcards_page" in repo
    assert "receiver_spirit_id" in repo
    assert ":owner_spirit_id" in repo
    router = ROUTER.read_text(encoding="utf-8")
    assert "load_postcard_page" in router
    assert "read_owned_postcard" in router
    assert "claimed_read_transaction" in router


def test_postcard_definer_hides_private_fields_and_user_id_arg() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in sql
    assert "p.receiver_spirit_id = caller" in sql
    assert "auth.uid()" in sql
    assert "p_user_id" not in sql
    assert "GRANT EXECUTE ON FUNCTION private.list_postcards_page(" in sql
    assert "TO kelin_api" in sql
    assert "user_id" not in sql.split("jsonb_build_object")[1].split("public_context")[0]
    assert "avatar" not in sql
    assert "memory" not in sql
    assert "latitude" not in sql
