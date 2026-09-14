from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from app.core.security import (
    ALLOWED_ALGS,
    JWKS_TTL,
    CurrentUser,
    JwksUnavailable,
    JwtAuthError,
    JwtVerifier,
    require_current_user,
)
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

ISSUER = "https://kelin-test.supabase.co/auth/v1"
AUDIENCE = "authenticated"
NOW = datetime.now(UTC)


class ScriptedFetcher:
    def __init__(
        self,
        documents: list[dict[str, Any]],
        *,
        fail_on: frozenset[int] = frozenset(),
    ) -> None:
        self.documents = documents
        self.fail_on = fail_on
        self.calls = 0

    async def fetch_jwks(self) -> dict[str, Any]:
        self.calls += 1
        if self.calls in self.fail_on:
            raise JwksUnavailable
        return self.documents[min(self.calls - 1, len(self.documents) - 1)]


def _rsa() -> tuple[RSAPrivateKey, RSAPublicKey]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _jwk(public: RSAPublicKey, kid: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(public))
    payload["kid"] = kid
    payload["alg"] = "RS256"
    payload["use"] = "sig"
    return payload


def _jwks(*keys: dict[str, Any]) -> dict[str, Any]:
    return {"keys": list(keys)}


def _claims(
    sub: str, *, exp_delta: timedelta = timedelta(hours=1), **extra: object
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "role": "authenticated",
        "exp": int((NOW + exp_delta).timestamp()),
        "iat": int(NOW.timestamp()),
    }
    body.update(extra)
    return body


def _token(private: RSAPrivateKey, kid: str, claims: dict[str, Any], *, alg: str = "RS256") -> str:
    return jwt.encode(claims, private, algorithm=alg, headers={"kid": kid, "alg": alg})


def _verifier(fetcher: ScriptedFetcher) -> JwtVerifier:
    return JwtVerifier(issuer=ISSUER, audience=AUDIENCE, fetcher=fetcher, clock=lambda: NOW)


def _unsigned(claims: dict[str, Any], kid: str) -> str:
    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header = _b64(json.dumps({"alg": "none", "typ": "JWT", "kid": kid}).encode())
    payload = _b64(json.dumps(claims).encode())
    return f"{header}.{payload}."


@pytest.mark.asyncio
async def test_valid_token_returns_current_user() -> None:
    private, public = _rsa()
    sub = uuid4()
    fetcher = ScriptedFetcher([_jwks(_jwk(public, "kid-a"))])
    user = await _verifier(fetcher).authenticate(_token(private, "kid-a", _claims(str(sub))))
    assert user.id == sub
    assert user.auth_role == "authenticated"
    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_cached_kid_does_not_refetch_within_ttl() -> None:
    private, public = _rsa()
    fetcher = ScriptedFetcher([_jwks(_jwk(public, "kid-a"))])
    verifier = _verifier(fetcher)
    token = _token(private, "kid-a", _claims(str(uuid4())))
    await verifier.authenticate(token)
    await verifier.authenticate(token)
    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_key_rotation_uses_new_kid_after_one_refresh() -> None:
    old_private, old_public = _rsa()
    new_private, new_public = _rsa()
    fetcher = ScriptedFetcher(
        [
            _jwks(_jwk(old_public, "kid-old")),
            _jwks(_jwk(old_public, "kid-old"), _jwk(new_public, "kid-new")),
        ]
    )
    verifier = _verifier(fetcher)
    await verifier.authenticate(_token(old_private, "kid-old", _claims(str(uuid4()))))
    user = await verifier.authenticate(_token(new_private, "kid-new", _claims(str(uuid4()))))
    assert user.auth_role == "authenticated"
    assert fetcher.calls == 2


@pytest.mark.asyncio
async def test_unknown_kid_refreshes_once_then_fails() -> None:
    _private, public = _rsa()
    fetcher = ScriptedFetcher([_jwks(_jwk(public, "kid-a")), _jwks(_jwk(public, "kid-a"))])
    verifier = _verifier(fetcher)
    other_private, _other_public = _rsa()
    token = _token(other_private, "kid-unknown", _claims(str(uuid4())))
    with pytest.raises(JwtAuthError):
        await verifier.authenticate(token)
    assert fetcher.calls == 1
    with pytest.raises(JwtAuthError):
        await verifier.authenticate(token)
    assert fetcher.calls == 2


@pytest.mark.asyncio
async def test_refresh_failure_does_not_accept_unsigned_or_hs256() -> None:
    rsa_private, rsa_public = _rsa()
    fetcher = ScriptedFetcher([_jwks(_jwk(rsa_public, "kid-a"))], fail_on=frozenset({2}))
    verifier = _verifier(fetcher)
    good = _token(rsa_private, "kid-a", _claims(str(uuid4())))
    await verifier.authenticate(good)
    verifier._fetched_at = NOW - JWKS_TTL - timedelta(seconds=1)
    still = await verifier.authenticate(good)
    assert still.auth_role == "authenticated"
    assert fetcher.calls == 2
    unsigned = _unsigned(_claims(str(uuid4())), "kid-a")
    with pytest.raises(JwtAuthError):
        await verifier.authenticate(unsigned)
    hs = jwt.encode(
        _claims(str(uuid4())),
        "not-a-secret",
        algorithm="HS256",
        headers={"kid": "kid-a"},
    )
    with pytest.raises(JwtAuthError):
        await verifier.authenticate(hs)


@pytest.mark.asyncio
async def test_expired_wrong_iss_aud_signature_and_role_fail() -> None:
    private, public = _rsa()
    other_private, _other_public = _rsa()
    fetcher = ScriptedFetcher([_jwks(_jwk(public, "kid-a"))])
    verifier = _verifier(fetcher)
    sub = str(uuid4())
    cases = [
        _token(private, "kid-a", _claims(sub, exp_delta=timedelta(hours=-1))),
        _token(private, "kid-a", _claims(sub, iss="https://other.example/auth/v1")),
        _token(private, "kid-a", _claims(sub, aud="service_role")),
        _token(other_private, "kid-a", _claims(sub)),
        _token(private, "kid-a", _claims(sub, role="service_role")),
        _token(private, "kid-a", _claims("not-a-uuid")),
        _unsigned(_claims(sub), "kid-a"),
    ]
    for token in cases:
        with pytest.raises(JwtAuthError):
            await verifier.authenticate(token)


@pytest.mark.asyncio
async def test_missing_kid_and_alg_none_fail() -> None:
    private, public = _rsa()
    fetcher = ScriptedFetcher([_jwks(_jwk(public, "kid-a"))])
    verifier = _verifier(fetcher)
    claims = _claims(str(uuid4()))
    no_kid = jwt.encode(claims, private, algorithm="RS256")
    with pytest.raises(JwtAuthError):
        await verifier.authenticate(no_kid)
    with pytest.raises(JwtAuthError):
        await verifier.authenticate(_unsigned(claims, "kid-a"))
    assert ALLOWED_ALGS == frozenset({"RS256"})


def test_bearer_requests_only_accept_valid_token() -> None:
    private, public = _rsa()
    fetcher = ScriptedFetcher([_jwks(_jwk(public, "kid-a"))])
    verifier = _verifier(fetcher)
    application = FastAPI()
    application.state.jwt_verifier = verifier

    @application.get("/whoami")
    async def whoami(user: CurrentUser = Depends(require_current_user)) -> dict[str, str]:
        return {"id": str(user.id), "role": user.auth_role}

    client = TestClient(application)
    sub = uuid4()
    token = _token(private, "kid-a", _claims(str(sub)))
    ok = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    assert ok.json() == {"id": str(sub), "role": "authenticated"}
    missing = client.get("/whoami")
    assert missing.status_code == 401
    expired = _token(private, "kid-a", _claims(str(sub), exp_delta=timedelta(hours=-1)))
    bad = client.get("/whoami", headers={"Authorization": f"Bearer {expired}"})
    assert bad.status_code == 401
    assert "eyJ" not in bad.text
    assert token not in bad.text


def test_source_never_disables_signature_verification() -> None:
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "app" / "core" / "security.py"
    text = source.read_text(encoding="utf-8")
    assert "verify_signature" in text
    assert 'verify_signature": False' not in text
    assert "verify_signature': False" not in text
    assert 'options={"verify_signature": False}' not in text
