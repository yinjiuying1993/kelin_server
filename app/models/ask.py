"""Ask dialogue, turns, and one rating row. V3-S2-T02.

The skill snapshot is copied at match time. It has no foreign key to craft_skills,
so a later skill edit or soft delete does not rewrite this dialogue.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_SLUG = "snapshot_skill_slug ~ '^[a-z0-9-]{1,64}$'"
_RANK = (
    "snapshot_rank_name IS NULL OR snapshot_rank_name IN ("
    "'门边学徒', '搭手帮工', '上手匠人', '独当师傅', '掌作名匠', '镇场宗师')"
)
_TEXT_500 = "char_length({name}) BETWEEN 1 AND 500"
_RATING = (
    "(rating = '不满意' AND points = 0) OR "
    "(rating = '满意' AND points = 5) OR "
    "(rating = '非常满意' AND points = 10)"
)
_TURN_REPLY = (
    "(status = 'succeeded' AND reply_text IS NOT NULL AND char_length(reply_text) >= 1) OR "
    "(status IN ('pending', 'failed') AND reply_text IS NULL)"
)


class AskQuestion(Base):
    __tablename__ = "ask_questions"
    __table_args__ = (
        UniqueConstraint("id", "asker_user_id", name="uq_ask_questions_id_asker"),
        UniqueConstraint(
            "asker_user_id", "client_id", name="uq_ask_questions_asker_client"
        ),
        CheckConstraint(
            "asker_user_id <> spirit_owner_user_id",
            name="ck_ask_questions_not_own_spirit",
        ),
        CheckConstraint(
            _TEXT_500.format(name="question_text"),
            name="ck_ask_questions_question_text",
        ),
        CheckConstraint(_SLUG, name="ck_ask_questions_skill_slug"),
        CheckConstraint(_RANK, name="ck_ask_questions_rank_name"),
        CheckConstraint(
            "char_length(snapshot_title) >= 1",
            name="ck_ask_questions_snapshot_title",
        ),
        CheckConstraint(
            "char_length(snapshot_detail) >= 1",
            name="ck_ask_questions_snapshot_detail",
        ),
        CheckConstraint(
            "char_length(snapshot_body) >= 1",
            name="ck_ask_questions_snapshot_body",
        ),
        Index(
            "ask_questions_history",
            "asker_user_id",
            "first_reply_at",
            postgresql_where=text("first_reply_at IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    asker_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    spirit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    spirit_owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_title: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_rank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_skill_slug: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_body: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_detail: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_reference_links: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    first_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AskTurn(Base):
    __tablename__ = "ask_turns"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ask_question_id", "asker_user_id"],
            ["public.ask_questions.id", "public.ask_questions.asker_user_id"],
            name="fk_ask_turns_question_asker",
            ondelete="CASCADE",
        ),
        UniqueConstraint("ask_question_id", "client_id", name="uq_ask_turns_question_client"),
        CheckConstraint(_TEXT_500.format(name="user_text"), name="ck_ask_turns_user_text"),
        CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="ck_ask_turns_status"),
        CheckConstraint(_TURN_REPLY, name="ck_ask_turns_reply"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ask_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asker_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_text: Mapped[str] = mapped_column(Text, nullable=False)
    reply_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AskRating(Base):
    __tablename__ = "ask_ratings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ask_question_id", "asker_user_id"],
            ["public.ask_questions.id", "public.ask_questions.asker_user_id"],
            name="fk_ask_ratings_question_asker",
            ondelete="CASCADE",
        ),
        UniqueConstraint("ask_question_id", name="uq_ask_ratings_question"),
        CheckConstraint(_RATING, name="ck_ask_ratings_points"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ask_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asker_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rating: Mapped[str] = mapped_column(Text, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
