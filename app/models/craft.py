"""Craft skill, match index, and self-test ticket tables. V3-S2-T01."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_SLUG = "skill_slug ~ '^[a-z0-9-]{1,64}$'"
_VISIBILITY = "visibility IN ('unpublished', 'public', 'private')"
_RANK = (
    "(points = 0 AND rank_name IS NULL) OR "
    "(points BETWEEN 1 AND 99 AND rank_name = '门边学徒') OR "
    "(points BETWEEN 100 AND 299 AND rank_name = '搭手帮工') OR "
    "(points BETWEEN 300 AND 599 AND rank_name = '上手匠人') OR "
    "(points BETWEEN 600 AND 999 AND rank_name = '独当师傅') OR "
    "(points BETWEEN 1000 AND 1499 AND rank_name = '掌作名匠') OR "
    "(points >= 1500 AND rank_name = '镇场宗师')"
)
_DRAFT = (
    "("
    "draft_title IS NULL AND draft_keywords IS NULL AND draft_detail IS NULL "
    "AND draft_reference_links IS NULL AND draft_body IS NULL"
    ") OR ("
    "draft_title IS NOT NULL AND draft_keywords IS NOT NULL AND draft_detail IS NOT NULL "
    "AND draft_reference_links IS NOT NULL AND draft_body IS NOT NULL"
    ")"
)
_ACTIVE_PAIR = (
    "(active_title IS NULL AND active_keywords IS NULL) OR "
    "(active_title IS NOT NULL AND active_keywords IS NOT NULL)"
)
_STAGED_PAIR = (
    "(staged_title IS NULL AND staged_keywords IS NULL) OR "
    "(staged_title IS NOT NULL AND staged_keywords IS NOT NULL)"
)


class CraftSkill(Base):
    __tablename__ = "craft_skills"
    __table_args__ = (
        UniqueConstraint("skill_slug", name="uq_craft_skills_skill_slug"),
        CheckConstraint(_SLUG, name="ck_craft_skills_skill_slug"),
        CheckConstraint(_VISIBILITY, name="ck_craft_skills_visibility"),
        CheckConstraint("points >= 0", name="ck_craft_skills_points"),
        CheckConstraint(_RANK, name="ck_craft_skills_rank"),
        CheckConstraint("cardinality(keywords) >= 1", name="ck_craft_skills_keywords"),
        CheckConstraint("char_length(title) >= 1", name="ck_craft_skills_title"),
        CheckConstraint("char_length(detail) >= 1", name="ck_craft_skills_detail"),
        CheckConstraint("char_length(body) >= 1", name="ck_craft_skills_body"),
        CheckConstraint(_DRAFT, name="ck_craft_skills_draft"),
        Index(
            "craft_skills_owner_recent",
            "spirit_id",
            "content_updated_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_slug: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    reference_links: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    draft_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    draft_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_reference_links: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    draft_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'unpublished'")
    )
    points: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rank_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CraftMatchIndex(Base):
    __tablename__ = "craft_match_index"
    __table_args__ = (
        UniqueConstraint("craft_skill_id", name="uq_craft_match_index_skill"),
        UniqueConstraint("skill_slug", name="uq_craft_match_index_skill_slug"),
        CheckConstraint(_SLUG, name="ck_craft_match_index_skill_slug"),
        CheckConstraint(_ACTIVE_PAIR, name="ck_craft_match_index_active_pair"),
        CheckConstraint(_STAGED_PAIR, name="ck_craft_match_index_staged_pair"),
        Index(
            "craft_match_index_active",
            "spirit_id",
            postgresql_where=text("active_title IS NOT NULL"),
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    craft_skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("craft_skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_slug: Mapped[str] = mapped_column(Text, nullable=False)
    active_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    staged_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    staged_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CraftSelfTestTicket(Base):
    __tablename__ = "craft_self_test_tickets"
    __table_args__ = (
        CheckConstraint(_SLUG, name="ck_craft_self_test_tickets_skill_slug"),
        CheckConstraint(
            "char_length(snapshot_body) >= 1",
            name="ck_craft_self_test_tickets_snapshot_body",
        ),
        Index("craft_self_test_tickets_expires", "expires_at"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    craft_skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("craft_skills.id", ondelete="CASCADE"),
        nullable=False,
    )
    skill_slug: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_body: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
