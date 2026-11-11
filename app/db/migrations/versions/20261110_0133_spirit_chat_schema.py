"""Spirit-chat tables, RLS, idempotency, and outbox.

Revision ID: 20261110_0133
Revises: 20261110_0132
Create Date: 2026-11-10

These tables are not ask, craft, or room tables. The rating worker gets no grant.
"""

from collections.abc import Sequence

from alembic import op
from app.db.rls_matrix import (
    RLS_MATRIX_V4,
    enable_force_statements,
    grant_statements,
    policy_create_statements,
)
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql

revision: str = "20261110_0133"
down_revision: str | None = "20261110_0132"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHAT_TABLES = (
    "spirit_chat_threads",
    "spirit_chat_participants",
    "spirit_chat_messages",
    "spirit_chat_idempotency",
    "spirit_chat_outbox",
)
_BODY = "char_length(body) BETWEEN 1 AND 500"
_IDEMPOTENCY = (
    "(status = 'in_progress' AND thread_id IS NULL AND message_id IS NULL "
    "AND message_seq IS NULL) OR "
    "(status = 'completed' AND thread_id IS NOT NULL AND message_seq IS NOT NULL)"
)


def _rows():
    return tuple(row for row in RLS_MATRIX_V4 if row.table in _CHAT_TABLES)


def upgrade() -> None:
    op.create_table(
        "spirit_chat_threads",
        Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=text("gen_random_uuid()"),
            nullable=False,
        ),
        Column("spirit_low_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("spirit_high_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint("id", name="pk_spirit_chat_threads"),
        ForeignKeyConstraint(
            ["spirit_low_id"],
            ["public.spirits.id"],
            name="fk_spirit_chat_threads_low",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["spirit_high_id"],
            ["public.spirits.id"],
            name="fk_spirit_chat_threads_high",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "spirit_low_id",
            "spirit_high_id",
            name="uq_spirit_chat_threads_pair",
        ),
        CheckConstraint(
            "spirit_low_id < spirit_high_id",
            name="ck_spirit_chat_threads_pair_order",
        ),
        schema="public",
    )
    op.create_table(
        "spirit_chat_participants",
        Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=text("gen_random_uuid()"),
            nullable=False,
        ),
        Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("my_spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("peer_spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("clear_seq", Integer(), server_default=text("0"), nullable=False),
        Column("last_read_seq", Integer(), server_default=text("0"), nullable=False),
        Column("last_visible_seq", Integer(), server_default=text("0"), nullable=False),
        Column("peer_display_name", Text(), nullable=False),
        Column("peer_avatar_url", Text(), nullable=False),
        Column("peer_available", Boolean(), nullable=False),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint("id", name="pk_spirit_chat_participants"),
        ForeignKeyConstraint(
            ["thread_id"],
            ["public.spirit_chat_threads.id"],
            name="fk_spirit_chat_participants_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["auth.users.id"],
            name="fk_spirit_chat_participants_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["my_spirit_id"],
            ["public.spirits.id"],
            name="fk_spirit_chat_participants_mine",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["peer_spirit_id"],
            ["public.spirits.id"],
            name="fk_spirit_chat_participants_peer",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "thread_id",
            "user_id",
            name="uq_spirit_chat_participants_thread_user",
        ),
        CheckConstraint(
            "my_spirit_id <> peer_spirit_id",
            name="ck_spirit_chat_participants_distinct_spirits",
        ),
        CheckConstraint(
            "clear_seq >= 0 AND last_read_seq >= 0 AND last_visible_seq >= 0",
            name="ck_spirit_chat_participants_seqs",
        ),
        CheckConstraint(
            "char_length(peer_display_name) BETWEEN 1 AND 20",
            name="ck_spirit_chat_participants_name",
        ),
        CheckConstraint(
            "char_length(peer_avatar_url) BETWEEN 1 AND 300",
            name="ck_spirit_chat_participants_avatar",
        ),
        schema="public",
    )
    op.create_index(
        "ix_spirit_chat_participants_user",
        "spirit_chat_participants",
        ["user_id"],
        schema="public",
    )
    op.create_table(
        "spirit_chat_messages",
        Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=text("gen_random_uuid()"),
            nullable=False,
        ),
        Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("seq", Integer(), nullable=False),
        Column("sender_spirit_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("body", Text(), nullable=False),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint("id", name="pk_spirit_chat_messages"),
        ForeignKeyConstraint(
            ["thread_id"],
            ["public.spirit_chat_threads.id"],
            name="fk_spirit_chat_messages_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["sender_spirit_id"],
            ["public.spirits.id"],
            name="fk_spirit_chat_messages_sender",
            ondelete="CASCADE",
        ),
        UniqueConstraint("thread_id", "seq", name="uq_spirit_chat_messages_thread_seq"),
        CheckConstraint("seq >= 1", name="ck_spirit_chat_messages_seq"),
        CheckConstraint(_BODY, name="ck_spirit_chat_messages_body"),
        schema="public",
    )
    op.create_table(
        "spirit_chat_idempotency",
        Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("request_hash", Text(), nullable=False),
        Column("status", Text(), nullable=False),
        Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        Column("message_seq", Integer(), nullable=True),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint(
            "user_id",
            "client_id",
            name="pk_spirit_chat_idempotency",
        ),
        ForeignKeyConstraint(
            ["user_id"],
            ["auth.users.id"],
            name="fk_spirit_chat_idempotency_user",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["thread_id"],
            ["public.spirit_chat_threads.id"],
            name="fk_spirit_chat_idempotency_thread",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["message_id"],
            ["public.spirit_chat_messages.id"],
            name="fk_spirit_chat_idempotency_message",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "user_id",
            "client_id",
            name="uq_spirit_chat_idempotency_user_client",
        ),
        CheckConstraint(
            "status IN ('in_progress', 'completed')",
            name="ck_spirit_chat_idempotency_status",
        ),
        CheckConstraint(
            "char_length(request_hash) = 64",
            name="ck_spirit_chat_idempotency_hash",
        ),
        CheckConstraint(_IDEMPOTENCY, name="ck_spirit_chat_idempotency_shape"),
        schema="public",
    )
    op.create_table(
        "spirit_chat_outbox",
        Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=text("gen_random_uuid()"),
            nullable=False,
        ),
        Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        Column("event_type", Text(), nullable=False),
        Column("seq", Integer(), nullable=False),
        Column("event_body", Text(), nullable=True),
        Column("processed_at", DateTime(timezone=True), nullable=True),
        Column(
            "created_at",
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
        PrimaryKeyConstraint("id", name="pk_spirit_chat_outbox"),
        ForeignKeyConstraint(
            ["recipient_user_id"],
            ["auth.users.id"],
            name="fk_spirit_chat_outbox_recipient",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["thread_id"],
            ["public.spirit_chat_threads.id"],
            name="fk_spirit_chat_outbox_thread",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "event_type IN ('message.created', 'thread.updated')",
            name="ck_spirit_chat_outbox_type",
        ),
        CheckConstraint("seq >= 0", name="ck_spirit_chat_outbox_seq"),
        schema="public",
    )
    op.create_index(
        "ix_spirit_chat_outbox_pending",
        "spirit_chat_outbox",
        ["created_at"],
        schema="public",
        postgresql_where=text("processed_at IS NULL"),
    )
    rows = _rows()
    for statement in enable_force_statements(rows):
        op.execute(statement)
    for statement in policy_create_statements(rows):
        op.execute(statement)
    for statement in grant_statements(rows):
        op.execute(statement)


def downgrade() -> None:
    op.drop_index("ix_spirit_chat_outbox_pending", table_name="spirit_chat_outbox")
    op.drop_table("spirit_chat_outbox")
    op.drop_table("spirit_chat_idempotency")
    op.drop_table("spirit_chat_messages")
    op.drop_index(
        "ix_spirit_chat_participants_user",
        table_name="spirit_chat_participants",
    )
    op.drop_table("spirit_chat_participants")
    op.drop_table("spirit_chat_threads")
