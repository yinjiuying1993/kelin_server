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
from app.schemas.memory import MemoryPage
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
ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "memories.py"
ACTIVE_TYPES = ("preference", "knowledge", "emotion", "relation", "speech", "sight")


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
    assert "load_memory_page" in source
    body = source
    while '"""' in body:
        start = body.find('"""')
        end = body.find('"""', start + 3)
        if end < 0:
            break
        body = body[:start] + body[end + 3 :]
    assert "OFFSET" not in body.upper()
    assert "SELECT" not in body
    assert "INSERT INTO" not in body


def test_memory_pages_filters_tombstones_cursor_and_isolation() -> None:
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

        missing_filter = client.get("/api/v1/memories?limit=2", headers=owner_auth)
        assert missing_filter.status_code == 422
        assert missing_filter.json()["error"]["code"] == "INVALID_INPUT"

        empty_page = client.get("/api/v1/memories?filter=all", headers=empty_auth)
        assert empty_page.status_code == 200, empty_page.text
        empty_data = MemoryPage.model_validate(empty_page.json()["data"])
        assert empty_data.items == []
        assert empty_data.tombstones == []
        assert empty_data.has_more is False
        assert empty_data.next_cursor is None

        spirit_id = await _spirit_id(url, owner)
        other_spirit_id = await _spirit_id(url, other)
        seeded = await _insert_owner_memories(url, spirit_id)
        await _insert_other_memories(url, other_spirit_id)
        public_ids = seeded["active"]
        sealed_id = seeded["sealed"]
        deleted_id = seeded["deleted"]

        first = client.get("/api/v1/memories?filter=all&limit=2", headers=owner_auth)
        assert first.status_code == 200, first.text
        first_page = MemoryPage.model_validate(first.json()["data"])
        assert len(first_page.items) == 2
        assert first_page.has_more is True
        assert first_page.next_cursor is not None
        assert first_page.next_cursor.startswith("kelin.mem.v1.")
        assert first_page.items[0].id == public_ids[0]
        assert first_page.items[1].id == public_ids[1]
        assert sealed_id not in {item.id for item in first_page.items}
        assert deleted_id not in {item.id for item in first_page.items}
        assert all(item.status == "active" for item in first_page.items)
        _assert_newest_first(first_page)
        assert [row.id for row in first_page.tombstones] == [deleted_id]
        raw_tombstone = first.json()["data"]["tombstones"][0]
        assert set(raw_tombstone.keys()) == {"id", "deleted_at"}
        assert "summary" not in raw_tombstone

        middle = client.get(
            "/api/v1/memories",
            params={"filter": "all", "cursor": first_page.next_cursor, "limit": 2},
            headers=owner_auth,
        )
        assert middle.status_code == 200, middle.text
        middle_page = MemoryPage.model_validate(middle.json()["data"])
        assert len(middle_page.items) == 2
        assert middle_page.has_more is True
        assert middle_page.next_cursor is not None
        assert middle_page.next_cursor != first_page.next_cursor
        assert middle_page.snapshot_at == first_page.snapshot_at
        assert middle_page.items[0].id == public_ids[2]
        assert middle_page.items[1].id == public_ids[3]
        _assert_newest_first(middle_page)

        last = client.get(
            "/api/v1/memories",
            params={"filter": "all", "cursor": middle_page.next_cursor, "limit": 2},
            headers=owner_auth,
        )
        assert last.status_code == 200, last.text
        last_page = MemoryPage.model_validate(last.json()["data"])
        assert len(last_page.items) == 2
        assert last_page.has_more is False
        assert last_page.next_cursor is None
        assert last_page.snapshot_at == first_page.snapshot_at
        assert last_page.items[0].id == public_ids[4]
        assert last_page.items[1].id == public_ids[5]
        _assert_newest_first(last_page)

        seen = [item.id for item in (*first_page.items, *middle_page.items, *last_page.items)]
        assert seen == public_ids
        assert sealed_id not in seen

        newer_id = uuid4()
        await _insert_one(
            url,
            spirit_id,
            memory_id=newer_id,
            memory_type="sight",
            created_at=None,
        )
        replay_middle = client.get(
            "/api/v1/memories",
            params={"filter": "all", "cursor": first_page.next_cursor, "limit": 2},
            headers=owner_auth,
        )
        replayed = MemoryPage.model_validate(replay_middle.json()["data"])
        assert newer_id not in {item.id for item in replayed.items}

        refreshed = client.get("/api/v1/memories?filter=all&limit=10", headers=owner_auth)
        refreshed_page = MemoryPage.model_validate(refreshed.json()["data"])
        assert refreshed_page.items[0].id == newer_id
        assert sealed_id not in {item.id for item in refreshed_page.items}

        mismatch = client.get(
            "/api/v1/memories",
            params={"filter": "knowledge", "cursor": first_page.next_cursor, "limit": 2},
            headers=owner_auth,
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["error"]["code"] == "INVALID_CURSOR"
        assert mismatch.json()["error"]["retryable"] is False

        invalid = client.get(
            "/api/v1/memories?filter=all&cursor=not-a-cursor",
            headers=owner_auth,
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_CURSOR"

        foreign = client.get(
            "/api/v1/memories",
            params={"filter": "all", "cursor": first_page.next_cursor, "limit": 2},
            headers=other_auth,
        )
        assert foreign.status_code == 422
        assert foreign.json()["error"]["code"] == "INVALID_CURSOR"

        other_first = client.get("/api/v1/memories?filter=all&limit=10", headers=other_auth)
        other_page = MemoryPage.model_validate(other_first.json()["data"])
        assert {item.id for item in other_page.items}.isdisjoint(set(public_ids))
        assert deleted_id not in {row.id for row in other_page.tombstones}

        by_filter = {
            name: MemoryPage.model_validate(
                client.get(f"/api/v1/memories?filter={name}&limit=50", headers=owner_auth).json()[
                    "data"
                ]
            )
            for name in ("all", "relationship", "knowledge", "speech", "sight")
        }
        type_by_id = {item.id: item.type for item in by_filter["all"].items}
        assert {type_by_id[item_id] for item_id in public_ids} == set(ACTIVE_TYPES)
        assert {item.type for item in by_filter["relationship"].items} <= {
            "preference",
            "relation",
            "emotion",
        }
        assert {item.id for item in by_filter["relationship"].items} == {
            public_ids[2],
            public_ids[3],
            public_ids[5],
        }
        assert {item.type for item in by_filter["knowledge"].items} == {"knowledge"}
        assert {item.type for item in by_filter["speech"].items} == {"speech"}
        assert {item.type for item in by_filter["sight"].items} == {"sight"}
        assert sealed_id not in {item.id for item in by_filter["knowledge"].items}
        assert [row.id for row in by_filter["speech"].tombstones] == [deleted_id]
        assert by_filter["knowledge"].tombstones == []


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


async def _insert_owner_memories(url: str, spirit_id: UUID) -> dict[str, Any]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    active_ids = [uuid4() for _ in ACTIVE_TYPES]
    sealed_id = uuid4()
    deleted_id = uuid4()
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        for index, (memory_id, memory_type) in enumerate(
            zip(active_ids, ACTIVE_TYPES, strict=True)
        ):
            await conn.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence, created_at"
                    ") VALUES ("
                    ":id, :spirit_id, :type, :summary, 80, 0.900, :created_at)"
                ),
                {
                    "id": memory_id,
                    "spirit_id": spirit_id,
                    "type": memory_type,
                    "summary": f"刻痕{index + 1}",
                    "created_at": base + timedelta(seconds=index),
                },
            )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, status, sealed_at, created_at"
                ") VALUES ("
                ":id, :spirit_id, 'knowledge', '已封存', 80, 0.900, 'sealed', :sealed_at, "
                ":created_at)"
            ),
            {
                "id": sealed_id,
                "spirit_id": spirit_id,
                "sealed_at": base + timedelta(seconds=20),
                "created_at": base + timedelta(seconds=20),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, salience, confidence, status, deleted_at, created_at"
                ") VALUES ("
                ":id, :spirit_id, 'speech', '已删除正文', 80, 0.900, 'deleted', :deleted_at, "
                ":created_at)"
            ),
            {
                "id": deleted_id,
                "spirit_id": spirit_id,
                "deleted_at": base + timedelta(seconds=21),
                "created_at": base + timedelta(seconds=21),
            },
        )
    await engine.dispose()
    active_ids.reverse()
    return {"active": active_ids, "sealed": sealed_id, "deleted": deleted_id}


async def _insert_other_memories(url: str, spirit_id: UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        for index in range(2):
            await conn.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence"
                    ") VALUES (:id, :spirit_id, 'preference', :summary, 70, 0.800)"
                ),
                {
                    "id": uuid4(),
                    "spirit_id": spirit_id,
                    "summary": f"他人{index + 1}",
                },
            )
    await engine.dispose()


async def _insert_one(
    url: str,
    spirit_id: UUID,
    *,
    memory_id: UUID,
    memory_type: str,
    created_at: datetime | None,
) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        if created_at is None:
            await conn.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence"
                    ") VALUES (:id, :spirit_id, :type, '新刻痕', 80, 0.900)"
                ),
                {"id": memory_id, "spirit_id": spirit_id, "type": memory_type},
            )
        else:
            await conn.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence, created_at"
                    ") VALUES (:id, :spirit_id, :type, '新刻痕', 80, 0.900, :created_at)"
                ),
                {
                    "id": memory_id,
                    "spirit_id": spirit_id,
                    "type": memory_type,
                    "created_at": created_at,
                },
            )
    await engine.dispose()


def _assert_newest_first(page: MemoryPage) -> None:
    previous: tuple[str, str] | None = None
    for item in page.items:
        key = (item.created_at, str(item.id))
        if previous is not None and key > previous:
            raise AssertionError("page items must be created_at desc, id desc")
        previous = key
        assert item.status == "active"
        assert 1 <= len(item.summary) <= 500
