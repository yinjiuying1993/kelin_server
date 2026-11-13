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
from app.schemas.bootstrap import BootstrapSnapshot
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.db.harness import upgrade_empty_kelin_test_to_head

ISSUER = "https://kelin-test.supabase.co/auth/v1"
AUDIENCE = "authenticated"
NOW = datetime.now(UTC)
ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "bootstrap.py"


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


def _spirit_body(*, client_id: UUID | None = None) -> dict[str, Any]:
    return {
        "client_id": str(client_id or uuid4()),
        "egg": "warm",
        "name": "未名",
        "consents": {
            "ai_disclosure": {"document_version": "2026-11-12", "explicitly_accepted": True},
            "data_notice": {"document_version": "2026-11-12", "displayed": True},
            "user_terms": {"document_version": "2026-11-12", "displayed": True},
        },
    }


def _app(*, url: str | None, private: RSAPrivateKey, public: RSAPublicKey) -> FastAPI:
    settings = Settings(
        app_env="test",
        database_url_api=SecretStr(url) if url is not None else None,
        supabase_jwt_issuer=ISSUER,
        supabase_jwt_audience=AUDIENCE,
        supabase_jwks_url="https://kelin-test.example/jwks.json",
    )
    application = create_app(settings)
    application.state.jwt_verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=_StaticJwks({"keys": [_jwk(public, "kid-a")]}),
        clock=lambda: NOW,
    )
    return application


async def _seed_user(url: str, user_id: UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_router_wires_facade_without_sql_or_provider() -> None:
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert "INSERT INTO" not in source
    assert "desired_spirit_state" not in source
    assert "FOR UPDATE" not in source
    assert "bailian" not in source.lower()
    assert "Provider" not in source
    assert "settle_spirit_state" not in source
    assert "run_bootstrap_snapshot" in source
    assert "claimed_transaction" in source
    assert "claimed_read_transaction" not in source


def test_missing_database_is_dependency_unavailable_not_partial_200() -> None:
    private, public = _rsa()
    token = _token(private, "kid-a", uuid4())
    application = _app(url=None, private=private, public=public)
    with TestClient(application) as client:
        response = client.get(
            "/api/v1/bootstrap",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 503
    payload = response.json()
    assert payload["ok"] is False
    assert payload["data"] is None
    assert payload["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    assert payload["error"]["retryable"] is True
    assert "spirit" not in payload


def test_new_and_hatched_users_return_full_snapshot() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_empty_and_hatched_shapes(url))


async def _assert_empty_and_hatched_shapes(url: str) -> None:
    private, public = _rsa()
    new_id = uuid4()
    hatched_id = uuid4()
    await _seed_user(url, new_id)
    await _seed_user(url, hatched_id)

    application = _app(url=url, private=private, public=public)
    new_token = _token(private, "kid-a", new_id)
    hatched_token = _token(private, "kid-a", hatched_id)
    with TestClient(application) as client:
        fresh = client.get(
            "/api/v1/bootstrap",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        created = client.post(
            "/api/v1/spirits",
            json=_spirit_body(),
            headers={"Authorization": f"Bearer {hatched_token}"},
        )
        hatched = client.get(
            "/api/v1/bootstrap",
            headers={"Authorization": f"Bearer {hatched_token}"},
        )
    assert created.status_code == 200, created.text
    assert fresh.status_code == 200, fresh.text
    assert hatched.status_code == 200, hatched.text
    new_snap = BootstrapSnapshot.model_validate(fresh.json()["data"])
    hatched_snap = BootstrapSnapshot.model_validate(hatched.json()["data"])
    assert fresh.json()["ok"] is True
    assert fresh.json()["error"] is None
    assert new_snap.spirit is None
    assert new_snap.room is None
    assert new_snap.snapshot_version == 0
    assert new_snap.model_dump().get("onboarding") is None
    assert "server_time" not in new_snap.model_dump()
    assert hatched_snap.spirit is not None
    assert hatched_snap.spirit.hatched_at is not None
    assert hatched_snap.room is not None
    assert hatched_snap.spirit.stage == "formed"
    assert hatched_snap.room.stage_image_key == "formed_image"
    assert hatched_snap.room.status == "home"
    room = hatched_snap.room.model_dump()
    assert set(room) == {
        "stage_image_key",
        "letter_key",
        "text_status",
        "status",
    }
    assert room["letter_key"] is None
    assert room["text_status"] == "home"
    assert "weather" not in room
    assert "layers" not in room
    assert "pending_sight" not in room
    assert hatched_snap.snapshot_version == hatched_snap.spirit.version
    dumped = fresh.text + hatched.text
    assert new_token not in dumped
    assert hatched_token not in dumped
    assert "eyJ" not in dumped

