"""V4-T03 chat tables stay out of the ask outbox and the shared idempotency table."""

from __future__ import annotations

from pathlib import Path

from app.db.rls_matrix import (
    RLS_MATRIX_V3,
    RLS_MATRIX_V4,
    enable_force_statements,
    grant_statements,
)
from app.repositories.spirit_chat import MESSAGE_COLUMNS, PARTICIPANT_COLUMNS

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "db"
    / "migrations"
    / "versions"
    / "20261110_0133_spirit_chat_schema.py"
)
_TABLES = {
    "spirit_chat_threads",
    "spirit_chat_participants",
    "spirit_chat_messages",
    "spirit_chat_idempotency",
    "spirit_chat_outbox",
}


def test_revision_follows_the_directory_migration() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    assert 'revision: str = "20261110_0133"' in source
    assert 'down_revision: str | None = "20261110_0132"' in source
    assert "uq_spirit_chat_threads_pair" in source
    assert "spirit_low_id < spirit_high_id" in source
    assert "uq_spirit_chat_idempotency_user_client" in source
    assert "char_length(body) BETWEEN 1 AND 500" in source
    for name in PARTICIPANT_COLUMNS:
        assert name in source
    assert "unread" not in source
    assert '"avatar"' not in source
    assert "idempotency_records" not in source
    assert "outbox_events" not in source
    assert "run_worker_tick" not in source


def test_message_columns_do_not_store_an_avatar() -> None:
    assert "avatar" not in MESSAGE_COLUMNS
    assert "body" in MESSAGE_COLUMNS
    assert "peer_avatar_url" in PARTICIPANT_COLUMNS


def test_rls_is_separate_from_the_v3_matrix_and_the_worker() -> None:
    assert {row.table for row in RLS_MATRIX_V4} == _TABLES
    assert _TABLES.isdisjoint({row.table for row in RLS_MATRIX_V3})
    forced = " ".join(enable_force_statements(RLS_MATRIX_V4))
    for table in _TABLES:
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in forced
        assert f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY" in forced
    grants = grant_statements(RLS_MATRIX_V4)
    rendered = " ".join(grants)
    assert "kelin_worker" not in rendered
    message_grants = [item for item in grants if "spirit_chat_messages" in item]
    assert message_grants
    assert all("UPDATE" not in item for item in message_grants)
    assert "GRANT UPDATE (processed_at) ON TABLE public.spirit_chat_outbox" in rendered
    assert "public.outbox_events" not in rendered
    assert "public.idempotency_records" not in rendered
