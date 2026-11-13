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
ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "onboarding.py"


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
            "ai_disclosure": {"document_version": "2026-11-12", "explicitly_accepted": True},
            "data_notice": {"document_version": "2026-11-12", "displayed": True},
            "user_terms": {"document_version": "2026-11-12", "displayed": True},
        },
    }


async def _seed_user(url: str, user_id: UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_router_does_not_contain_settlement_sql() -> None:
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert "INSERT INTO" not in source
    assert "hatched_at =" not in source
    assert "complete_onboarding" in source
    assert "onboarding_complete_result_from_settlement" in source


def test_missing_expected_version_is_invalid_input_envelope() -> None:
    private, public = _rsa()
    sub = uuid4()
    settings = Settings(
        app_env="test",
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
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/onboarding/complete",
            json={"client_id": str(uuid4())},
            headers={"Authorization": f"Bearer {_token(private, 'kid-a', sub)}"},
        )
    assert response.status_code == 404
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"
    assert payload["data"] is None


def test_hatch_on_create_rejects_complete_gate() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_already_hatched(url))


async def _assert_already_hatched(url: str) -> None:
    private, public = _rsa()
    sub = uuid4()
    await _seed_user(url, sub)
    settings = Settings(
        app_env="test",
        database_url_api=SecretStr(url),
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
    token = _token(private, "kid-a", sub)
    auth = {"Authorization": f"Bearer {token}"}
    with TestClient(application) as client:
        created = client.post("/api/v1/spirits", json=_spirit_body(), headers=auth)
        assert created.status_code == 200, created.text
        version = created.json()["data"]["patch"]["spirit"]["version"]
        complete = client.post(
            "/api/v1/onboarding/complete",
            json={"client_id": str(uuid4()), "expected_spirit_version": version},
            headers=auth,
        )
    assert complete.status_code == 404
    payload = complete.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"
    assert payload["data"] is None
    assert token not in complete.text
    assert "eyJ" not in complete.text
