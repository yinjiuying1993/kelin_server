from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import jwt
from app.core.config import Settings
from app.core.security import JwtVerifier
from app.main import create_app
from app.schemas.messages import MessagePage
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

ISSUER = "https://kelin-test.supabase.co/auth/v1"
AUDIENCE = "authenticated"
NOW = datetime.now(UTC)
ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "messages.py"


class _StaticJwks:
    def __init__(self, document: dict[str, Any]) -> None:
        self._document = document

    async def fetch_jwks(self) -> dict[str, Any]:
        return self._document


def _rsa() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _jwk(public: RSAPublicKey, kid: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(public))
    payload["kid"] = kid
    payload["alg"] = "RS256"
    payload["use"] = "sig"
    return payload


def _token(private: RSAPrivateKey, kid: str, sub: UUID) -> str:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(sub),
        "role": "authenticated",
        "exp": int((NOW + timedelta(hours=1)).timestamp()),
        "iat": int(NOW.timestamp()),
    }
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid, "alg": "RS256"})


def _spirit_body() -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "egg": "warm",
        "name": "未名",
        "consents": {
            "ai_disclosure": {"document_version": "2026-09", "explicitly_accepted": True},
            "data_notice": {"document_version": "2026-09", "displayed": True},
            "user_terms": {"document_version": "2026-09", "displayed": True},
        },
    }


def test_router_uses_read_claim_and_has_no_sql() -> None:
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert "claimed_read_transaction" in source
    assert "load_message_page" in source
    assert "OFFSET" not in source.upper()
    assert "SELECT" not in source
    assert "INSERT INTO" not in source


def test_message_pages_empty_first_middle_last_invalid_and_snapshot() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_pages(url))


async def _assert_pages(url: str) -> None:
    private, public = _rsa()
    owner = uuid4()
    other = uuid4()
    empty = uuid4()
    await _seed_user(url, owner)
    await _seed_user(url, other)
    await _seed_user(url, empty)
    settings = Settings(
        app_env="test",
        database_url_api=SecretStr(url),
        supabase_jwt_issuer=ISSUER,
        supabase_jwt_audience=AUDIENCE,
        supabase_jwks_url="https://kelin-test.example/jwks.json",
        cursor_hmac_secret=SecretStr("http-test-cursor-hmac"),
    )
    application = create_app(settings)
    application.state.jwt_verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=_StaticJwks({"keys": [_jwk(public, "kid-a")]}),
        clock=lambda: NOW,
    )
    owner_auth = {"Authorization": f"Bearer {_token(private, 'kid-a', owner)}"}
    other_auth = {"Authorization": f"Bearer {_token(private, 'kid-a', other)}"}
    empty_auth = {"Authorization": f"Bearer {_token(private, 'kid-a', empty)}"}
    with TestClient(application) as client:
        created = client.post("/api/v1/spirits", json=_spirit_body(), headers=owner_auth)
        assert created.status_code == 200, created.text
        other_spirit = client.post("/api/v1/spirits", json=_spirit_body(), headers=other_auth)
        assert other_spirit.status_code == 200, other_spirit.text
        empty_page = client.get("/api/v1/messages", headers=empty_auth)
        assert empty_page.status_code == 200, empty_page.text
        empty_data = MessagePage.model_validate(empty_page.json()["data"])
        assert empty_data.items == []
        assert empty_data.has_more is False
        assert empty_data.next_cursor is None

        spirit_id = await _spirit_id(url, owner)
        other_spirit_id = await _spirit_id(url, other)
        public_ids = await _insert_history(url, spirit_id)
        await _insert_history(url, other_spirit_id, count=2)

        first = client.get("/api/v1/messages?limit=2", headers=owner_auth)
        assert first.status_code == 200, first.text
        first_page = MessagePage.model_validate(first.json()["data"])
        assert len(first_page.items) == 2
        assert first_page.has_more is True
        assert first_page.next_cursor is not None
        assert first_page.items[0].id == public_ids[0]
        assert first_page.items[1].id == public_ids[1]
        _assert_newest_first(first_page)

        middle = client.get(
            "/api/v1/messages",
            params={"cursor": first_page.next_cursor, "limit": 2},
            headers=owner_auth,
        )
        assert middle.status_code == 200, middle.text
        middle_page = MessagePage.model_validate(middle.json()["data"])
        assert len(middle_page.items) == 2
        assert middle_page.has_more is True
        assert middle_page.next_cursor is not None
        assert middle_page.next_cursor != first_page.next_cursor
        assert middle_page.snapshot_at == first_page.snapshot_at
        assert middle_page.items[0].id == public_ids[2]
        assert middle_page.items[1].id == public_ids[3]
        _assert_newest_first(middle_page)

        last = client.get(
            "/api/v1/messages",
            params={"cursor": middle_page.next_cursor, "limit": 2},
            headers=owner_auth,
        )
        assert last.status_code == 200, last.text
        last_page = MessagePage.model_validate(last.json()["data"])
        assert len(last_page.items) == 2
        assert last_page.has_more is False
        assert last_page.next_cursor is None
        assert last_page.snapshot_at == first_page.snapshot_at
        assert last_page.items[0].id == public_ids[4]
        assert last_page.items[1].id == public_ids[5]
        _assert_newest_first(last_page)

        seen = [item.id for item in (*first_page.items, *middle_page.items, *last_page.items)]
        assert seen == public_ids

        newer_id = uuid4()
        await _insert_one(url, spirit_id, message_id=newer_id, role="user", created_at=None)
        replay_middle = client.get(
            "/api/v1/messages",
            params={"cursor": first_page.next_cursor, "limit": 2},
            headers=owner_auth,
        )
        replayed = MessagePage.model_validate(replay_middle.json()["data"])
        assert newer_id not in {item.id for item in replayed.items}

        refreshed = client.get("/api/v1/messages?limit=10", headers=owner_auth)
        refreshed_page = MessagePage.model_validate(refreshed.json()["data"])
        assert refreshed_page.items[0].id == newer_id

        invalid = client.get("/api/v1/messages?cursor=not-a-cursor", headers=owner_auth)
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_CURSOR"
        assert invalid.json()["error"]["retryable"] is False

        foreign = client.get(
            "/api/v1/messages",
            params={"cursor": first_page.next_cursor, "limit": 2},
            headers=other_auth,
        )
        assert foreign.status_code == 422
        assert foreign.json()["error"]["code"] == "INVALID_CURSOR"

        other_first = client.get("/api/v1/messages?limit=10", headers=other_auth)
        other_page = MessagePage.model_validate(other_first.json()["data"])
        assert {item.id for item in other_page.items}.isdisjoint(set(public_ids))


async def _seed_user(url: str, user_id: UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _spirit_id(url: str, user_id: UUID) -> UUID:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT id FROM public.spirits WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
    await engine.dispose()
    assert value is not None
    return UUID(str(value))


async def _insert_history(url: str, spirit_id: UUID, *, count: int = 6) -> list[UUID]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ids = [uuid4() for _ in range(count)]
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO public.messages ("
                "id, spirit_id, client_id, role, content, source, status, created_at"
                ") VALUES ("
                ":id, :spirit_id, NULL, 'system', 'secret', 'text', 'accepted', :created_at)"
            ),
            {
                "id": uuid4(),
                "spirit_id": spirit_id,
                "created_at": base + timedelta(seconds=count + 5),
            },
        )
        for index, message_id in enumerate(ids):
            created_at = base + timedelta(seconds=index)
            if index % 2 == 0:
                await conn.execute(
                    text(
                        "INSERT INTO public.messages ("
                        "id, spirit_id, client_id, role, content, source, status, created_at"
                        ") VALUES ("
                        ":id, :spirit_id, :client_id, 'user', '你好', 'text', 'accepted', "
                        ":created_at)"
                    ),
                    {
                        "id": message_id,
                        "spirit_id": spirit_id,
                        "client_id": uuid4(),
                        "created_at": created_at,
                    },
                )
            else:
                await conn.execute(
                    text(
                        "INSERT INTO public.messages ("
                        "id, spirit_id, client_id, role, content, source, status, "
                        "reply_to_message_id, created_at"
                        ") VALUES ("
                        ":id, :spirit_id, NULL, 'spirit', '嗯。', 'text', 'generated', "
                        ":reply_to, :created_at)"
                    ),
                    {
                        "id": message_id,
                        "spirit_id": spirit_id,
                        "reply_to": ids[index - 1],
                        "created_at": created_at,
                    },
                )
    await engine.dispose()
    ids.reverse()
    return ids


async def _insert_one(
    url: str,
    spirit_id: UUID,
    *,
    message_id: UUID,
    role: str,
    created_at: datetime | None,
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        if created_at is None:
            await conn.execute(
                text(
                    "INSERT INTO public.messages ("
                    "id, spirit_id, client_id, role, content, source, status"
                    ") VALUES ("
                    ":id, :spirit_id, :client_id, :role, '你好', 'text', 'accepted')"
                ),
                {
                    "id": message_id,
                    "spirit_id": spirit_id,
                    "client_id": uuid4() if role == "user" else None,
                    "role": role,
                },
            )
        else:
            await conn.execute(
                text(
                    "INSERT INTO public.messages ("
                    "id, spirit_id, client_id, role, content, source, status, created_at"
                    ") VALUES ("
                    ":id, :spirit_id, :client_id, :role, '你好', 'text', 'accepted', :created_at)"
                ),
                {
                    "id": message_id,
                    "spirit_id": spirit_id,
                    "client_id": uuid4() if role == "user" else None,
                    "role": role,
                    "created_at": created_at,
                },
            )
    await engine.dispose()


def _assert_newest_first(page: MessagePage) -> None:
    previous: tuple[str, str] | None = None
    for item in page.items:
        key = (item.created_at, str(item.id))
        if previous is not None and key > previous:
            raise AssertionError("page items must be created_at desc, id desc")
        previous = key
        if item.role == "user":
            assert item.client_id is not None
        if item.role == "spirit":
            assert item.client_id is None
