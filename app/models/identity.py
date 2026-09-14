from __future__ import annotations

import uuid
from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuthUser(Base):
    """Local PostgreSQL stand-in for Supabase auth.users. Not Auth implementation."""

    __tablename__ = "users"
    __table_args__ = {"schema": "auth"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


class AccountConsent(Base):
    __tablename__ = "account_consents"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "consent_type",
            "document_version",
            name="uq_account_consents_user_type_version",
        ),
        CheckConstraint(
            "consent_type IN ('ai_disclosure', 'user_terms', 'data_notice')",
            name="ck_account_consents_type",
        ),
        CheckConstraint(
            "char_length(document_version) BETWEEN 1 AND 32",
            name="ck_account_consents_document_version_len",
        ),
        CheckConstraint("source = 'ios'", name="ck_account_consents_source_ios"),
        CheckConstraint(
            "assertion IN ('explicitly_accepted', 'displayed')",
            name="ck_account_consents_assertion",
        ),
        CheckConstraint(
            "(consent_type = 'ai_disclosure' AND assertion = 'explicitly_accepted') OR "
            "(consent_type IN ('user_terms', 'data_notice') AND assertion = 'displayed')",
            name="ck_account_consents_assertion_matches_type",
        ),
        CheckConstraint(
            "consent_type <> 'ai_disclosure' OR withdrawn_at IS NULL",
            name="ck_account_consents_ai_disclosure_not_withdrawn",
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_type: Mapped[str] = mapped_column(Text, nullable=False)
    document_version: Mapped[str] = mapped_column(Text, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    locale: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ios'"))
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    assertion: Mapped[str] = mapped_column(Text, nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint(
            "char_length(timezone) BETWEEN 1 AND 64",
            name="ck_user_preferences_timezone_len",
        ),
        CheckConstraint(
            "default_city IS NULL OR char_length(default_city) BETWEEN 1 AND 40",
            name="ck_user_preferences_default_city_len",
        ),
        CheckConstraint("version >= 1", name="ck_user_preferences_version_positive"),
        {"schema": "public"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tts_on: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    push_on: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    visit_on: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    dnd_start: Mapped[time] = mapped_column(Time, nullable=False, server_default=text("'23:00:00'"))
    dnd_end: Mapped[time] = mapped_column(Time, nullable=False, server_default=text("'08:00:00'"))
    timezone: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'Asia/Shanghai'"),
    )
    default_city: Mapped[str | None] = mapped_column(Text, nullable=True)
    location_weather_on: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    remote_search_on: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class Spirit(Base):
    __tablename__ = "spirits"
    __table_args__ = (
        UniqueConstraint("user_id", "client_id", name="uq_spirits_user_id_client_id"),
        CheckConstraint("char_length(name) BETWEEN 1 AND 20", name="ck_spirits_name_length"),
        CheckConstraint("egg IN ('warm', 'cold', 'wild')", name="ck_spirits_egg"),
        CheckConstraint("stage IN ('whelp', 'formed', 'awake')", name="ck_spirits_stage"),
        CheckConstraint("status IN ('home', 'away', 'study', 'lost')", name="ck_spirits_status"),
        CheckConstraint(
            "invite_code ~ '^[A-HJ-NP-Z2-9]{8}$'",
            name="ck_spirits_invite_code",
        ),
        CheckConstraint(
            "closeness BETWEEN 0 AND 100 AND curiosity BETWEEN 0 AND 100 "
            "AND sharpness BETWEEN 0 AND 100 AND nocturnal BETWEEN 0 AND 100 "
            "AND stubborn BETWEEN 0 AND 100 AND hunger BETWEEN 0 AND 100 "
            "AND energy BETWEEN 0 AND 100 AND mood BETWEEN 0 AND 100 "
            "AND bond BETWEEN 0 AND 100",
            name="ck_spirits_trait_ranges",
        ),
        CheckConstraint("onboarding_step BETWEEN 0 AND 5", name="ck_spirits_onboarding_step"),
        CheckConstraint("ordinary_dialogue_rounds >= 0", name="ck_spirits_ordinary_rounds"),
        CheckConstraint(
            "(onboarding_completed_at IS NULL) OR (onboarding_step = 5 AND hatched_at IS NOT NULL)",
            name="ck_spirits_onboarding_complete",
        ),
        {"schema": "public"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'未名'"))
    egg: Mapped[str] = mapped_column(Text, nullable=False)
    invite_code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    closeness: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    curiosity: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sharpness: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    nocturnal: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    stubborn: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    hunger: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("80"))
    energy: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("80"))
    mood: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("60"))
    bond: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'whelp'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'home'"))
    scholar_marks: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    onboarding_step: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ordinary_dialogue_rounds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    away_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    study_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_interact_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    hatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_visited: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    has_been_lost: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
