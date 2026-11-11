"""V4-T02 directory outcomes and the two extra public-profile fields."""

from __future__ import annotations

import inspect
from uuid import UUID

import pytest
from app.domain.spirit_directory import (
    DirectoryOutcome,
    DirectoryRow,
    classify_directory,
)
from app.domain.spirit_profile import avatar_url, current_avatar_key, display_name
from app.repositories import spirit_directory as directory_sql
from app.repositories import spirit_public as profile_sql
from app.services.spirit_directory import lookup_directory

_VIEWER = UUID("00000000-0000-4000-8000-0000000000c1")
_OWNER = UUID("00000000-0000-4000-8000-0000000000c2")
_SPIRIT = UUID("00000000-0000-4000-8000-0000000000c3")


class _Reader:
    def __init__(self, row: DirectoryRow | None) -> None:
        self.row = row

    async def lookup(self, viewer_id: UUID, spirit_id: UUID) -> DirectoryRow | None:
        del viewer_id, spirit_id
        return self.row


def _row(*, owner: UUID = _OWNER, available: bool = True) -> DirectoryRow:
    return DirectoryRow(
        spirit_id=_SPIRIT,
        owner_user_id=owner,
        display_name="小名",
        avatar_key="spirit-avatar/default-1",
        available=available,
    )


def test_unset_nickname_uses_the_original_name_and_default_avatar() -> None:
    assert display_name("原名", None) == "原名"
    assert display_name("原名", "  ") == "原名"
    assert display_name("原名", "小名") == "小名"
    assert current_avatar_key("spirit-avatar/default-1", None) == "spirit-avatar/default-1"
    assert current_avatar_key("spirit-avatar/default-1", "spirit-avatar/custom") == (
        "spirit-avatar/custom"
    )


@pytest.mark.asyncio
async def test_missing_own_and_hidden_spirits_are_not_found() -> None:
    missing = await lookup_directory(_Reader(None), viewer_id=_VIEWER, spirit_id=_SPIRIT)
    assert missing.outcome is DirectoryOutcome.MISSING
    assert missing.card is None
    own = classify_directory(_row(owner=_VIEWER), viewer_id=_VIEWER)
    assert own.outcome is DirectoryOutcome.MISSING
    assert own.card is None


@pytest.mark.asyncio
async def test_available_and_disabled_cards_keep_only_five_fields() -> None:
    opened = await lookup_directory(_Reader(_row()), viewer_id=_VIEWER, spirit_id=_SPIRIT)
    assert opened.outcome is DirectoryOutcome.AVAILABLE
    assert opened.card is not None
    assert opened.card.owner_user_id == _OWNER
    assert opened.card.display_name == "小名"
    assert opened.card.avatar_url == avatar_url("spirit-avatar/default-1")
    assert set(opened.card.__dataclass_fields__) == {
        "spirit_id",
        "owner_user_id",
        "display_name",
        "avatar_url",
        "available",
    }
    closed = classify_directory(_row(available=False), viewer_id=_VIEWER)
    assert closed.outcome is DirectoryOutcome.UNAVAILABLE
    assert closed.card is not None
    assert closed.card.available is False


def test_directory_sql_does_not_read_chat_or_skill_tables() -> None:
    directory = inspect.getsource(directory_sql)
    for forbidden in ("craft_skills", "ask_questions", "room_messages", "memories", "spirit_chat"):
        assert forbidden not in directory
    public = inspect.getsource(profile_sql)
    for forbidden in ("invite_code", "companion_days", "spirit_chat", "ask_questions"):
        assert forbidden not in public
