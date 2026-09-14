from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.domain.cursor import (
    CURSOR_VERSION,
    FRIENDS_CURSOR_PREFIX,
    InvalidCursor,
    MessageCursor,
    decode_friend_cursor,
    encode_friend_cursor,
    friends_filter_hash,
    messages_filter_hash,
)
from app.domain.friends import friend_add_hash, friend_remove_hash, ordered_spirit_pair

SECRET = b"unit-test-cursor-hmac"
OWNER = UUID("00000000-0000-4000-8000-0000000000aa")
OTHER = UUID("00000000-0000-4000-8000-0000000000bb")
NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "app" / "db" / "migrations" / "versions" / "20260908_0012_friend_definer.py"
REPO = ROOT / "app" / "repositories" / "friends.py"
SERVICE = ROOT / "app" / "services" / "friends.py"
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


def test_ordered_spirit_pair_is_undirected_low_lt_high() -> None:
    low, high = ordered_spirit_pair(OTHER, OWNER)
    assert low < high
    assert ordered_spirit_pair(OWNER, OTHER) == (low, high)
    with pytest.raises(ValueError):
        ordered_spirit_pair(OWNER, OWNER)


def test_friend_request_hash_changes_with_payload() -> None:
    add_a = friend_add_hash(invite_code="ABCD2345")
    add_b = friend_add_hash(invite_code="ABCD2346")
    assert add_a != add_b
    assert add_a == friend_add_hash(invite_code="ABCD2345")
    friend_id = uuid4()
    assert friend_remove_hash(friend_id=friend_id) != friend_remove_hash(friend_id=uuid4())


def test_friends_cursor_binds_owner_and_rejects_message_prefix() -> None:
    owner_hash = friends_filter_hash(OWNER)
    assert owner_hash != friends_filter_hash(OTHER)
    cursor = MessageCursor(
        version=CURSOR_VERSION,
        sort_time=NOW - timedelta(seconds=3),
        id=UUID("00000000-0000-4000-8000-000000000001"),
        snapshot_at=NOW,
        filter_hash=owner_hash,
    )
    token = encode_friend_cursor(cursor, secret=SECRET)
    assert token.startswith(FRIENDS_CURSOR_PREFIX)
    decoded = decode_friend_cursor(token, secret=SECRET, expected_filter_hash=owner_hash)
    assert decoded.id == cursor.id
    with pytest.raises(InvalidCursor):
        decode_friend_cursor(token, secret=SECRET, expected_filter_hash=friends_filter_hash(OTHER))
    with pytest.raises(InvalidCursor):
        decode_friend_cursor(token, secret=SECRET, expected_filter_hash=messages_filter_hash(OWNER))


def test_friends_sql_is_keyset_owner_filtered_without_offset() -> None:
    for path in (REPO, SERVICE, ROUTER):
        source = _source_without_docs(path)
        assert "OFFSET" not in source.upper()
    repo = REPO.read_text(encoding="utf-8")
    assert "private.list_friends_page" in repo
    assert "owner_spirit_id" in repo
    assert "LIMIT :fetch_limit" not in repo or "fetch_limit" in repo
    router = ROUTER.read_text(encoding="utf-8")
    assert "claimed_read_transaction" in router
    assert "claimed_transaction" in router
    assert "load_friend_page" in router
    assert "add_owned_friend" in router
    assert "remove_owned_friend" in router
    assert "list_postcards" in router
    assert "DEPENDENCY_UNAVAILABLE" in router


def test_friend_definer_migration_has_no_user_id_arg() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public, private" in sql
    assert "private.add_friend_by_code(p_invite_code text)" in sql
    assert "p_user_id" not in sql
    assert "auth.uid()" in sql
    assert "GRANT EXECUTE ON FUNCTION private.add_friend_by_code(text) TO kelin_api" in sql
    assert "GRANT EXECUTE ON FUNCTION private.remove_friend_edge(uuid) TO kelin_api" in sql
    assert "REVOKE ALL ON FUNCTION private.add_friend_by_code(text) FROM PUBLIC" in sql
    assert "spirit_low_id" in sql and "spirit_high_id" in sql
    assert "EXECUTE ON FUNCTION private.add_friend_by_code(text) TO kelin_worker" not in sql
    assert "OWNER TO {_DEFINER}" in sql
    assert "user_id, avatar" not in sql
