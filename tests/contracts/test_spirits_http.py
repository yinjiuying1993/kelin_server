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
from app.domain.invite_code import invite_code_is_valid
from app.main import create_app
from app.schemas.spirit import MutationResult
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
ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "spirits.py"


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


def _valid_body(
    *,
    egg: str = "warm",
    name: str = "未名",
    client_id: UUID | None = None,
) -> dict[str, Any]:
    return {
        "client_id": str(client_id or uuid4()),
        "egg": egg,
        "name": name,
        "consents": {
            "ai_disclosure": {"document_version": "2026-09", "explicitly_accepted": True},
            "data_notice": {"document_version": "2026-09", "displayed": True},
            "user_terms": {"document_version": "2026-09", "displayed": True},
        },
    }


def _client_with_jwt(private: RSAPrivateKey, public: RSAPublicKey) -> TestClient:
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
    return TestClient(application)


def test_router_does_not_contain_settlement_sql_or_traits() -> None:
    source = ROUTER_PATH.read_text(encoding="utf-8")
    assert "INSERT INTO" not in source
    assert "EGG_TRAITS" not in source
    assert "traits_for_egg" not in source
    assert "create_spirit_if_absent" in source
    assert "mutation_result_from_create" in source


def test_missing_client_id_is_invalid_input_envelope() -> None:
    private, public = _rsa()
    sub = uuid4()
    body = _valid_body()
    del body["client_id"]
    with _client_with_jwt(private, public) as client:
        response = client.post(
            "/api/v1/spirits",
            json=body,
            headers={"Authorization": f"Bearer {_token(private, 'kid-a', sub)}"},
        )
    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert payload["data"] is None
    assert payload["request_id"] == response.headers["X-Request-ID"]


def test_illegal_egg_is_invalid_input_envelope() -> None:
    private, public = _rsa()
    sub = uuid4()
    body = _valid_body()
    body["egg"] = "hot"
    with _client_with_jwt(private, public) as client:
        response = client.post(
            "/api/v1/spirits",
            json=body,
            headers={"Authorization": f"Bearer {_token(private, 'kid-a', sub)}"},
        )
    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_INPUT"


def test_success_and_idempotency_conflict_use_stable_envelope() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_success_and_replay_conflict(url))


def test_verified_jwt_provisions_local_auth_user_without_preseed() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_provision_without_preseed(url))


async def _seed_user(url: str, user_id: UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _assert_success_and_replay_conflict(url: str) -> None:
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
    client_id = uuid4()
    body = _valid_body(client_id=client_id)
    with TestClient(application) as client:
        first = client.post(
            "/api/v1/spirits",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        replay = client.post(
            "/api/v1/spirits",
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        conflict_body = _valid_body(name="雾生", client_id=client_id)
        conflict = client.post(
            "/api/v1/spirits",
            json=conflict_body,
            headers={"Authorization": f"Bearer {token}"},
        )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["ok"] is True
    assert payload["error"] is None
    data = MutationResult.model_validate(payload["data"])
    assert data.patch.spirit is not None
    assert data.patch.spirit.egg == "warm"
    assert data.patch.spirit.closeness == 65
    assert data.patch.preferences is None
    assert data.patch.onboarding is not None
    assert data.patch.onboarding.step == 0
    assert data.patch.snapshot_version == 1
    assert invite_code_is_valid(data.patch.spirit.invite_code)
    assert replay.status_code == 200
    replayed = MutationResult.model_validate(replay.json()["data"])
    assert replayed.resource.id == data.resource.id
    assert replayed.patch.spirit is not None
    assert replayed.patch.spirit.invite_code == data.patch.spirit.invite_code
    assert conflict.status_code == 409
    err = conflict.json()
    assert err["ok"] is False
    assert err["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert err["error"]["retryable"] is False
    assert err["data"] is None
    dumped = first.text + replay.text + conflict.text
    assert token not in dumped
    assert "eyJ" not in dumped


async def _assert_provision_without_preseed(url: str) -> None:
    private, public = _rsa()
    sub = uuid4()
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
    with TestClient(application) as client:
        created = client.post(
            "/api/v1/spirits",
            json=_valid_body(),
            headers={"Authorization": f"Bearer {token}"},
        )
        bootstrap = client.get(
            "/api/v1/bootstrap",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert created.status_code == 200, created.text
    assert bootstrap.status_code == 200, bootstrap.text
    assert MutationResult.model_validate(created.json()["data"]).patch.spirit is not None
