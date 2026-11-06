"""Craft ledger and one settlement row per ask dialogue. V3-S2-T03.

Points stay on these tables. There is no foreign key to growth, feed, or bond.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CraftLedger(Base):
    __tablename__ = "craft_ledgers"
    __table_args__ = (
        UniqueConstraint("spirit_id", name="uq_craft_ledgers_spirit"),
        CheckConstraint("total_points >= 0", name="ck_craft_ledgers_total_points"),
        CheckConstraint("help_count >= 0", name="ck_craft_ledgers_help_count"),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    spirit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.spirits.id", ondelete="CASCADE"),
        nullable=False,
    )
    total_points: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    help_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CraftSettlement(Base):
    __tablename__ = "craft_settlements"
    __table_args__ = (
        UniqueConstraint("ask_question_id", name="uq_craft_settlements_question"),
        CheckConstraint("points_added IN (0, 5, 10)", name="ck_craft_settlements_points"),
        CheckConstraint("help_counted", name="ck_craft_settlements_help_counted"),
        CheckConstraint(
            "skill_points_applied = false OR points_added > 0",
            name="ck_craft_settlements_skill_points",
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    ask_question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.ask_questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    spirit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    points_added: Mapped[int] = mapped_column(Integer, nullable=False)
    help_counted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    skill_points_applied: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
