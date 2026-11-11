"""V4-T08 publishes a face after the spirit commit and only refreshes chat copies."""

from __future__ import annotations

import base64
import json
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from app.core.errors import ApiError
from app.domain.spirit_chat import OutboxDelivery
from app.domain.spirit_face import SpiritFace, subscribe_spirit_face
from app.domain.spirit_profile import avatar_url, default_avatar_key
from app.providers.errors import ProviderError
from app.repositories.spirit_profile import OwnerProfile
from app.services.spirit_chat.delivery import client_event, push_deliveries
from app.services.spirit_chat.hub import Hub
from app.services.spirit_profile import restore_default_avatar, save_avatar, save_nickname

_OWNER = UUID("00000000-0000-4000-8000-0000000000c1")
_SPIRIT = UUID("00000000-0000-4000-8000-0000000000c2")
_PEER = UUID("00000000-0000-4000-8000-0000000000c3")
_THREAD = UUID("00000000-0000-4000-8000-0000000000c4")
_NOW = datetime(2026, 11, 10, 8, 0, tzinfo=UTC)
_JPEG = b"\xff\xd8\xff" + b"avatar"


def _row(**overrides: object) -> OwnerProfile:
    base = OwnerProfile(
        spirit_id=_SPIRIT,
        name="原名",
        nickname="小名",
        stage="whelp",
        invite_code="ABCDEFGH",
        hatched_at=_NOW,
        default_avatar_key=default_avatar_key(_OWNER),
        custom_avatar_key=None,
        help_count=3,
        skills=(),
    )
    return OwnerProfile(**{**base.__dict__, **overrides})


class _Store:
    def __init__(self, row: OwnerProfile) -> None:
        self.row = row

    async def load(self, user_id: UUID) -> OwnerProfile | None:
        del user_id
        return self.row

    async def set_nickname(self, user_id: UUID, nickname: str | None) -> bool:
        del user_id
        self.row = _row(nickname=nickname, custom_avatar_key=self.row.custom_avatar_key)
        return True

    async def set_custom_avatar(self, user_id: UUID, avatar_key: str | None) -> bool:
        del user_id
        self.row = _row(nickname=self.row.nickname, custom_avatar_key=avatar_key)
        return True


class _Safety:
    def __init__(self, decision: str | Exception = "allow") -> None:
        self.decision_value = decision

    async def decision(self, payload: object) -> str:
        del payload
        if isinstance(self.decision_value, Exception):
            raise self.decision_value
        return self.decision_value


class _Objects:
    def put(self, key: str, body: bytes) -> None:
        del key, body


class _Heard:
    def __init__(self) -> None:
        self.faces: list[SpiritFace] = []

    async def __call__(self, face: SpiritFace) -> None:
        self.faces.append(face)


class _Sock:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_rejected_save_does_not_publish_and_success_has_only_the_face() -> None:
    heard = _Heard()
    cancel = subscribe_spirit_face(heard)
    try:
        store = _Store(_row())
        with pytest.raises(ApiError):
            await save_nickname(
                store, _Safety(), user_id=_OWNER, nickname="1234567890123", now=_NOW
            )
        with pytest.raises(ApiError):
            await save_nickname(
                store, _Safety("block"), user_id=_OWNER, nickname="新名", now=_NOW
            )
        with pytest.raises(ApiError):
            await save_nickname(
                store,
                _Safety(ProviderError("PROVIDER_TIMEOUT")),
                user_id=_OWNER,
                nickname="新名",
                now=_NOW,
            )
        assert heard.faces == []
        assert store.row.nickname == "小名"
        page = await save_nickname(store, _Safety(), user_id=_OWNER, nickname="新名", now=_NOW)
        assert page.nickname == "新名"
        assert [field.name for field in fields(SpiritFace)] == [
            "spirit_id",
            "display_name",
            "avatar_url",
            "available",
        ]
        face = heard.faces[-1]
        assert face.spirit_id == _SPIRIT
        assert face.display_name == "新名"
        assert face.available is True
        assert face.display_name != "ABCDEFGH"
    finally:
        cancel()


@pytest.mark.asyncio
async def test_cleared_nickname_avatar_and_default_publish_the_current_face() -> None:
    heard = _Heard()
    cancel = subscribe_spirit_face(heard)
    try:
        store = _Store(_row())
        await save_nickname(store, _Safety(), user_id=_OWNER, nickname="  ", now=_NOW)
        assert heard.faces[-1].display_name == "原名"
        encoded = base64.b64encode(_JPEG).decode("ascii")
        await save_avatar(
            store, _Safety(), _Objects(), user_id=_OWNER, encoded=encoded, now=_NOW
        )
        assert heard.faces[-1].avatar_url == avatar_url(f"spirit-avatar/{_SPIRIT}/custom")
        await restore_default_avatar(store, user_id=_OWNER, now=_NOW)
        assert heard.faces[-1].avatar_url == avatar_url(default_avatar_key(_OWNER))
        assert all(item.available is True for item in heard.faces)
    finally:
        cancel()


@pytest.mark.asyncio
async def test_online_peer_sees_the_new_face_and_the_owner_keeps_the_peer_row() -> None:
    peer = _Sock()
    owner = _Sock()
    hub = Hub()
    await hub.attach(_PEER, peer)
    await hub.attach(_OWNER, owner)
    peer_body = {
        "text": "你好",
        "unread": 1,
        "peer_spirit_id": str(_SPIRIT),
        "display_name": "新名",
        "avatar_url": "https://kelin.invalid/spirit-avatar/new",
        "available": False,
        "hidden": False,
    }
    owner_body = {
        "text": "你好",
        "unread": 0,
        "peer_spirit_id": str(_PEER),
        "display_name": "对方",
        "avatar_url": "https://kelin.invalid/peer",
        "available": True,
        "hidden": False,
        "own_display_name": "新名",
        "own_avatar_url": "https://kelin.invalid/spirit-avatar/new",
    }
    rows = [
        OutboxDelivery(
            UUID("00000000-0000-4000-8000-0000000000d1"),
            _PEER,
            _THREAD,
            "thread.updated",
            2,
            json.dumps(peer_body),
        ),
        OutboxDelivery(
            UUID("00000000-0000-4000-8000-0000000000d2"),
            _OWNER,
            _THREAD,
            "thread.updated",
            2,
            json.dumps(owner_body),
        ),
    ]

    async def visible(user_id, thread_id, seq, hidden) -> bool:
        del user_id, thread_id, hidden
        return seq > 1

    pushed = await push_deliveries(rows, visible=visible, hub=hub)
    assert pushed == 2
    assert peer.sent[0]["data"]["display_name"] == "新名"
    assert peer.sent[0]["data"]["available"] is False
    assert "own_display_name" not in peer.sent[0]["data"]
    assert owner.sent[0]["data"]["display_name"] == "对方"
    assert owner.sent[0]["data"]["peer_spirit_id"] == str(_PEER)
    assert owner.sent[0]["data"]["own_avatar_url"].endswith("/new")
    assert "user_id" not in json.dumps(peer.sent + owner.sent)
    assert client_event(rows[1])["data"]["own_display_name"] == "新名"


def test_face_sql_updates_copies_without_joining_the_spirit_write() -> None:
    root = Path(__file__).resolve().parents[2]
    migration = (
        root / "app" / "db" / "migrations" / "versions" / "20261110_0136_spirit_chat_face.py"
    ).read_text(encoding="utf-8")
    profile = (root / "app" / "services" / "spirit_profile.py").read_text(encoding="utf-8")
    settings = (root / "app" / "api" / "v1" / "settings.py").read_text(encoding="utf-8")
    account = (root / "app" / "api" / "v1" / "account.py").read_text(encoding="utf-8")
    send = (
        root / "app" / "db" / "migrations" / "versions" / "20261110_0134_spirit_chat_send.py"
    ).read_text(encoding="utf-8")
    faces = (root / "app" / "services" / "spirit_chat" / "faces.py").read_text(encoding="utf-8")
    assert "UPDATE public.spirit_chat_participants" in migration
    assert "WHERE seen.peer_spirit_id = target" in migration
    assert "'display_name', mine.peer_display_name" in migration
    assert "'own_display_name', face_name" in migration
    assert "invite_code" not in migration
    assert "companion" not in migration
    assert "UPDATE public.spirits" not in migration
    assert "DELETE" not in migration.upper()
    assert "TO kelin_api" in migration
    assert "kelin_worker" not in migration
    assert "spirit_chat" not in profile
    assert settings.index("async with claimed_transaction") < settings.index(
        "await publish_spirit_face"
    )
    assert account.index("async with claimed_transaction") < account.index(
        "await publish_spirit_face"
    )
    assert "claimed_transaction(factory, None)" in faces
    assert "peer_display_name = EXCLUDED.peer_display_name" in send
