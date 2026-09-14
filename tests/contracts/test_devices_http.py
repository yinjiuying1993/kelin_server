"""P17-T01 HTTP POST /devices."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
from app.core.config import Settings
from app.core.security import JwtVerifier
from app.main import create_app
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

from tests.db.harness import upgrade_empty_kelin_test_to_head

ISSUER = "https://kelin-test.supabase.co/auth/v1"
AUDIENCE = "authenticated"
NOW = datetime.now(UTC)
TOKEN = "d" * 64
TOKEN_ROTATED = "e" * 64


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


def _app(url: str, private: RSAPrivateKey, public: RSAPublicKey) -> FastAPI:
    settings = Settings(
        app_env="test",
        database_url_api=SecretStr(url),
        supabase_jwt_issuer=ISSUER,
        supabase_jwt_audience=AUDIENCE,
        supabase_jwks_url="https://kelin-test.example/jwks.json",
        cursor_hmac_secret=SecretStr("kelin-p17-cursor-hmac"),
    )
    application = create_app(settings)
    application.state.jwt_verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=_StaticJwks({"keys": [_jwk(public, "kid-a")]}),
        clock=lambda: NOW,
    )
    return application


def test_devices_http_rotate_disable_and_environment() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_http(url))


async def _assert_http(url: str) -> None:
    private, public = _rsa()
    user = uuid4()
    token = _token(private, "kid-a", user)
    installation = str(uuid4())
    app = _app(url, private, public)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {token}", "X-Request-ID": str(uuid4())}
        created = client.post(
            "/api/v1/devices",
            headers=headers,
            json={
                "client_id": str(uuid4()),
                "installation_id": installation,
                "apns_token": TOKEN,
                "environment": "sandbox",
                "enabled": True,
                "app_version": "1.0.0",
                "locale": "zh-Hans",
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["ok"] is True
        device_id = body["data"]["device_id"]
        assert body["data"]["enabled"] is True
        rotated = client.post(
            "/api/v1/devices",
            headers=headers,
            json={
                "client_id": str(uuid4()),
                "installation_id": installation,
                "apns_token": TOKEN_ROTATED,
                "environment": "sandbox",
                "enabled": True,
            },
        )
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["data"]["device_id"] == device_id
        disabled = client.post(
            "/api/v1/devices",
            headers=headers,
            json={
                "client_id": str(uuid4()),
                "installation_id": installation,
                "apns_token": TOKEN_ROTATED,
                "environment": "sandbox",
                "enabled": False,
            },
        )
        assert disabled.json()["data"]["enabled"] is False
        mismatch = client.post(
            "/api/v1/devices",
            headers=headers,
            json={
                "client_id": str(uuid4()),
                "installation_id": installation,
                "apns_token": TOKEN_ROTATED,
                "environment": "production",
                "enabled": True,
            },
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["error"]["code"] == "INVALID_INPUT"
        missing = client.post(
            "/api/v1/devices",
            headers=headers,
            json={
                "client_id": str(uuid4()),
                "installation_id": installation,
                "environment": "sandbox",
            },
        )
        assert missing.status_code == 422
        blob = created.text + rotated.text + disabled.text + mismatch.text
        assert TOKEN not in blob
        assert TOKEN_ROTATED not in blob
