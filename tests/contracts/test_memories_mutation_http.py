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
from app.schemas.memory import CLEAR_ALL_MEMORIES, MemoryMutationResult, MemoryPage, MemoryResource
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


def test_mutation_router_has_no_sql() -> None:
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert "claimed_transaction" in source
    assert "patch_owned_memory" in source
    assert "delete_owned_memory" in source
    assert "clear_owned_memories" in source
    assert "INSERT INTO" not in source
    assert "UPDATE public" not in source


def test_memory_mutations_correct_seal_delete_clear_and_errors() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_http(url))


async def _assert_http(url: str) -> None:
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
        other_created = client.post("/api/v1/spirits", json=_spirit_body(), headers=other_auth)
        assert other_created.status_code == 200, other_created.text
        spirit_id = await _spirit_id(url, owner)
        ids = await _insert_memories(url, spirit_id)
        correct_id = ids["correct"]
        seal_id = ids["seal"]
        delete_id = ids["delete"]

        missing = client.get("/api/v1/memories?filter=all", headers=owner_auth)
        assert missing.status_code == 200

        client_id = str(uuid4())
        correct_body = {
            "client_id": client_id,
            "expected_version": 1,
            "action": "correct",
            "summary": "请叫我阿年",
        }
        first = client.patch(
            f"/api/v1/memories/{correct_id}", json=correct_body, headers=owner_auth
        )
        assert first.status_code == 200, first.text
        first_result = MemoryMutationResult.model_validate(first.json()["data"])
        assert isinstance(first_result.resource, MemoryResource)
        assert first_result.resource.status == "active"
        assert first_result.resource.version == 2
        assert first_result.patch.memories_upsert
        replay = client.patch(
            f"/api/v1/memories/{correct_id}", json=correct_body, headers=owner_auth
        )
        assert replay.status_code == 200, replay.text
        replay_result = MemoryMutationResult.model_validate(replay.json()["data"])
        assert isinstance(replay_result.resource, MemoryResource)
        assert replay_result.resource.version == 2

        conflict_hash = client.patch(
            f"/api/v1/memories/{correct_id}",
            json={**correct_body, "summary": "请叫我小年"},
            headers=owner_auth,
        )
        assert conflict_hash.status_code == 409
        assert conflict_hash.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        version_conflict = client.patch(
            f"/api/v1/memories/{correct_id}",
            json={
                "client_id": str(uuid4()),
                "expected_version": 1,
                "action": "correct",
                "summary": "请叫我小年",
            },
            headers=owner_auth,
        )
        assert version_conflict.status_code == 409
        assert version_conflict.json()["error"]["code"] == "CONFLICT"

        unknown = client.patch(
            f"/api/v1/memories/{uuid4()}",
            json={"client_id": str(uuid4()), "expected_version": 1, "action": "seal"},
            headers=owner_auth,
        )
        assert unknown.status_code == 404

        empty_patch = client.patch(
            f"/api/v1/memories/{correct_id}",
            json={"client_id": str(uuid4()), "expected_version": 2, "action": "seal"},
            headers=empty_auth,
        )
        assert empty_patch.status_code == 404

        foreign = client.patch(
            f"/api/v1/memories/{correct_id}",
            json={"client_id": str(uuid4()), "expected_version": 2, "action": "seal"},
            headers=other_auth,
        )
        assert foreign.status_code == 404

        sealed = client.patch(
            f"/api/v1/memories/{seal_id}",
            json={"client_id": str(uuid4()), "expected_version": 1, "action": "seal"},
            headers=owner_auth,
        )
        assert sealed.status_code == 200, sealed.text
        sealed_result = MemoryMutationResult.model_validate(sealed.json()["data"])
        assert isinstance(sealed_result.resource, MemoryResource)
        assert sealed_result.resource.status == "sealed"
        listed = MemoryPage.model_validate(
            client.get("/api/v1/memories?filter=all&limit=50", headers=owner_auth).json()["data"]
        )
        listed_ids = {item.id for item in listed.items}
        assert correct_id in listed_ids
        assert seal_id not in listed_ids
        assert all(item.status == "active" for item in listed.items)

        not_active = client.patch(
            f"/api/v1/memories/{seal_id}",
            json={
                "client_id": str(uuid4()),
                "expected_version": sealed_result.resource.version,
                "action": "correct",
                "summary": "不该纠正",
            },
            headers=owner_auth,
        )
        assert not_active.status_code == 409
        assert not_active.json()["error"]["code"] == "MEMORY_NOT_ACTIVE"

        deleted = client.request(
            "DELETE",
            f"/api/v1/memories/{delete_id}",
            json={"client_id": str(uuid4()), "expected_version": 1},
            headers=owner_auth,
        )
        assert deleted.status_code == 200, deleted.text
        deleted_result = MemoryMutationResult.model_validate(deleted.json()["data"])
        assert isinstance(deleted_result.resource, MemoryResource)
        assert deleted_result.resource.status == "deleted"
        assert deleted_result.patch.memories_upsert == []
        assert len(deleted_result.patch.memory_tombstones) == 1
        stone = deleted_result.patch.memory_tombstones[0]
        parsed = stone if isinstance(stone, dict) else stone
        if isinstance(parsed, dict):
            assert set(parsed.keys()) == {"id", "deleted_at"}
            assert "summary" not in parsed
        after_delete = MemoryPage.model_validate(
            client.get("/api/v1/memories?filter=all&limit=50", headers=owner_auth).json()["data"]
        )
        assert delete_id not in {item.id for item in after_delete.items}
        assert delete_id in {row.id for row in after_delete.tombstones}

        bad_confirm = client.request(
            "DELETE",
            "/api/v1/memories",
            json={"client_id": str(uuid4()), "confirm": "CLEAR_ALL"},
            headers=owner_auth,
        )
        assert bad_confirm.status_code == 422
        assert bad_confirm.json()["error"]["code"] == "INVALID_INPUT"

        clear_client = str(uuid4())
        cleared = client.request(
            "DELETE",
            "/api/v1/memories",
            json={"client_id": clear_client, "confirm": CLEAR_ALL_MEMORIES},
            headers=owner_auth,
        )
        assert cleared.status_code == 200, cleared.text
        clear_result = MemoryMutationResult.model_validate(cleared.json()["data"])
        assert clear_result.resource.type == "memory_clear"
        assert str(clear_result.resource.id) == clear_client
        replay_clear = client.request(
            "DELETE",
            "/api/v1/memories",
            json={"client_id": clear_client, "confirm": CLEAR_ALL_MEMORIES},
            headers=owner_auth,
        )
        assert replay_clear.status_code == 200, replay_clear.text
        empty_page = MemoryPage.model_validate(
            client.get("/api/v1/memories?filter=all", headers=owner_auth).json()["data"]
        )
        assert empty_page.items == []
        active_samples = await _active_sample_count(url, spirit_id)
        assert active_samples == 0


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


async def _insert_memories(url: str, spirit_id: UUID) -> dict[str, UUID]:
    ids = {"correct": uuid4(), "seal": uuid4(), "delete": uuid4()}
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        for index, (key, memory_id) in enumerate(ids.items()):
            await conn.execute(
                text(
                    "INSERT INTO public.memories ("
                    "id, spirit_id, type, summary, salience, confidence, created_at"
                    ") VALUES ("
                    ":id, :spirit_id, 'preference', :summary, 80, 0.900, :created_at)"
                ),
                {
                    "id": memory_id,
                    "spirit_id": spirit_id,
                    "summary": f"{key}记忆",
                    "created_at": datetime(2026, 4, 1, tzinfo=UTC) + timedelta(seconds=index),
                },
            )
        await conn.execute(
            text(
                "INSERT INTO public.style_samples (spirit_id, kind, text) "
                "VALUES (:spirit_id, 'user_dialect', '咱')"
            ),
            {"spirit_id": spirit_id},
        )
    await engine.dispose()
    return ids


async def _active_sample_count(url: str, spirit_id: UUID) -> int:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        value = await conn.scalar(
            text(
                "SELECT count(*) FROM public.style_samples "
                "WHERE spirit_id = :spirit_id AND status = 'active'"
            ),
            {"spirit_id": spirit_id},
        )
    await engine.dispose()
    return int(value or 0)
