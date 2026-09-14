"""P16-T09 HTTP dual-account request IDs, whitelist, and C 404."""

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
from app.schemas.social_api import PRIVATE_SOCIAL_FIELD_NAMES, PUBLIC_PROFILE_FIELD_NAMES
from app.schemas.spirit import MutationResult
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


def _spirit_body(*, name: str) -> dict[str, Any]:
    return {
        "client_id": str(uuid4()),
        "egg": "warm",
        "name": name,
        "consents": {
            "ai_disclosure": {"document_version": "2026-09", "explicitly_accepted": True},
            "data_notice": {"document_version": "2026-09", "displayed": True},
            "user_terms": {"document_version": "2026-09", "displayed": True},
        },
    }


def _app(url: str, private: RSAPrivateKey, public: RSAPublicKey) -> FastAPI:
    settings = Settings(
        app_env="test",
        database_url_api=SecretStr(url),
        supabase_jwt_issuer=ISSUER,
        supabase_jwt_audience=AUDIENCE,
        supabase_jwks_url="https://kelin-test.example/jwks.json",
        cursor_hmac_secret=SecretStr("kelin-t09-cursor-hmac"),
    )
    application = create_app(settings)
    application.state.jwt_verifier = JwtVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        fetcher=_StaticJwks({"keys": [_jwk(public, "kid-a")]}),
        clock=lambda: NOW,
    )
    return application


def _auth(token: str, request_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Request-ID": request_id}


def _walk_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(str(key) for key in value)
        for item in value.values():
            found.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_walk_keys(item))
    return found


def test_social_http_dual_account_request_ids_and_third_party_404() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_http(url))


async def _assert_http(url: str) -> None:
    private, public = _rsa()
    user_a = uuid4()
    user_b = uuid4()
    user_c = uuid4()
    token_a = _token(private, "kid-a", user_a)
    token_b = _token(private, "kid-a", user_b)
    token_c = _token(private, "kid-a", user_c)
    req_a = "0199a016-0000-4000-8000-00000000000a"
    req_b = "0199a016-0000-4000-8000-00000000000b"
    req_c = "0199a016-0000-4000-8000-00000000000c"
    application = _app(url, private, public)
    with TestClient(application) as client:
        created_a = client.post(
            "/api/v1/spirits", json=_spirit_body(name="甲灵"), headers=_auth(token_a, req_a)
        )
        created_b = client.post(
            "/api/v1/spirits", json=_spirit_body(name="乙灵"), headers=_auth(token_b, req_b)
        )
        created_c = client.post(
            "/api/v1/spirits", json=_spirit_body(name="丙灵"), headers=_auth(token_c, req_c)
        )
        assert created_a.status_code == 200, created_a.text
        assert created_b.status_code == 200, created_b.text
        assert created_c.status_code == 200, created_c.text
        spirit_a = MutationResult.model_validate(created_a.json()["data"]).patch.spirit
        spirit_b = MutationResult.model_validate(created_b.json()["data"]).patch.spirit
        assert spirit_a is not None and spirit_b is not None
        spaced = f"  {spirit_b.invite_code.lower()}  "
        added = client.post(
            "/api/v1/friends",
            json={"client_id": str(uuid4()), "invite_code": spaced},
            headers=_auth(token_a, req_a),
        )
        assert added.status_code == 200, added.text
        assert added.json()["request_id"] == req_a == added.headers["X-Request-ID"]
        from_b = client.post(
            "/api/v1/friends",
            json={"client_id": str(uuid4()), "invite_code": spirit_a.invite_code},
            headers=_auth(token_b, req_b),
        )
        assert from_b.status_code == 200, from_b.text
        assert from_b.json()["request_id"] == req_b
        assert req_a != req_b
        self_add = client.post(
            "/api/v1/friends",
            json={"client_id": str(uuid4()), "invite_code": spirit_a.invite_code},
            headers=_auth(token_a, req_a),
        )
        assert self_add.status_code == 409
        assert self_add.json()["error"]["code"] == "SELF_FRIEND_NOT_ALLOWED"
        listed_a = client.get("/api/v1/friends", headers=_auth(token_a, req_a))
        listed_b = client.get("/api/v1/friends", headers=_auth(token_b, req_b))
        listed_c = client.get("/api/v1/friends", headers=_auth(token_c, req_c))
        assert listed_a.status_code == 200
        assert listed_b.status_code == 200
        assert listed_c.status_code == 200
        items_a = listed_a.json()["data"]["items"]
        items_b = listed_b.json()["data"]["items"]
        assert len(items_a) == 1
        assert len(items_b) == 1
        assert items_a[0]["friend_id"] == items_b[0]["friend_id"]
        assert set(items_a[0]["spirit"]) == PUBLIC_PROFILE_FIELD_NAMES
        assert listed_c.json()["data"]["items"] == []
        friend_id = items_a[0]["friend_id"]
        stolen = client.request(
            "DELETE",
            f"/api/v1/friends/{friend_id}",
            json={"client_id": str(uuid4())},
            headers=_auth(token_c, req_c),
        )
        assert stolen.status_code == 404
        assert stolen.json()["request_id"] == req_c
        missing_card = client.patch(
            f"/api/v1/postcards/{uuid4()}/read",
            json={"client_id": str(uuid4())},
            headers=_auth(token_c, req_c),
        )
        assert missing_card.status_code == 404
        cards_c = client.get("/api/v1/postcards", headers=_auth(token_c, req_c))
        assert cards_c.status_code == 200
        assert cards_c.json()["data"]["items"] == []
        visits = client.post("/api/v1/visits", json={"client_id": str(uuid4())}, headers=_auth(token_a, req_a))
        assert visits.status_code == 404
        dumped = (
            added.text
            + from_b.text
            + listed_a.text
            + listed_b.text
            + listed_c.text
            + stolen.text
        )
        assert token_a not in dumped
        assert token_b not in dumped
        assert "eyJ" not in dumped
        assert _walk_keys(listed_a.json()["data"]).isdisjoint(PRIVATE_SOCIAL_FIELD_NAMES)
