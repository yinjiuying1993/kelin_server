from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import jwt
from app.core.config import Settings
from app.core.logging import hash_user_id
from app.core.security import CurrentUser, JwtVerifier
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.main import create_app
from app.repositories import spirit as spirit_repo
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
NAME_A = "甲灵隐私"
NAME_B = "乙灵越权"
CONSENT_A = "priv-consent-a1"
CONSENT_B = "priv-consent-b2"


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


def _body(*, name: str, version: str, client_id: UUID, egg: str = "warm") -> dict[str, Any]:
    return {
        "client_id": str(client_id),
        "egg": egg,
        "name": name,
        "consents": {
            "ai_disclosure": {"document_version": version, "explicitly_accepted": True},
            "data_notice": {"document_version": version, "displayed": True},
            "user_terms": {"document_version": version, "displayed": True},
        },
    }


async def _seed_user(url: str, user_id: UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


def test_a_cannot_create_or_read_b_and_logs_omit_private_fields() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_cross_account_privacy(url))


async def _assert_cross_account_privacy(url: str) -> None:
    private, public = _rsa()
    user_a = uuid4()
    user_b = uuid4()
    await _seed_user(url, user_a)
    await _seed_user(url, user_b)
    settings = Settings(
        app_env="test",
        database_url_api=SecretStr(url),
        supabase_jwt_issuer=ISSUER,
        supabase_jwt_audience=AUDIENCE,
        supabase_jwks_url="https://kelin-test.example/jwks.json",
    )
    token_a = _token(private, "kid-a", user_a)
    token_b = _token(private, "kid-a", user_b)
    client_id = uuid4()
    logs = StringIO()
    with patch("sys.stdout", logs):
        application = create_app(settings)
        application.state.jwt_verifier = JwtVerifier(
            issuer=ISSUER,
            audience=AUDIENCE,
            fetcher=_StaticJwks({"keys": [_jwk(public, "kid-a")]}),
            clock=lambda: NOW,
        )
        with TestClient(application) as client:
            created_a = client.post(
                "/api/v1/spirits",
                json=_body(name=NAME_A, version=CONSENT_A, client_id=client_id),
                headers={"Authorization": f"Bearer {token_a}"},
            )
            created_b = client.post(
                "/api/v1/spirits",
                json=_body(name=NAME_B, version=CONSENT_B, client_id=client_id),
                headers={"Authorization": f"Bearer {token_b}"},
            )
            replay_a = client.post(
                "/api/v1/spirits",
                json=_body(name=NAME_A, version=CONSENT_A, client_id=client_id),
                headers={"Authorization": f"Bearer {token_a}"},
            )
            invalid = client.post(
                "/api/v1/spirits",
                json=_body(name=NAME_A, version=CONSENT_A, client_id=uuid4(), egg="hot"),
                headers={"Authorization": f"Bearer {token_a}"},
            )
            stolen = client.get(
                "/api/v1/spirits",
                headers={"Authorization": f"Bearer {token_a}"},
            )
    assert created_a.status_code == 200, created_a.text
    assert created_b.status_code == 200, created_b.text
    assert replay_a.status_code == 200
    data_a = MutationResult.model_validate(created_a.json()["data"])
    data_b = MutationResult.model_validate(created_b.json()["data"])
    replayed = MutationResult.model_validate(replay_a.json()["data"])
    assert data_a.patch.spirit is not None
    assert data_b.patch.spirit is not None
    assert data_a.resource.id != data_b.resource.id
    assert data_a.patch.spirit.name == NAME_A
    assert data_b.patch.spirit.name == NAME_B
    assert data_a.patch.spirit.invite_code != data_b.patch.spirit.invite_code
    assert replayed.resource.id == data_a.resource.id
    assert NAME_B not in created_a.text
    assert NAME_A not in created_b.text
    assert data_a.patch.spirit.invite_code not in created_b.text
    assert data_b.patch.spirit.invite_code not in created_a.text
    assert invalid.status_code == 422
    invalid_text = invalid.text
    assert CONSENT_A not in invalid_text
    assert NAME_A not in invalid_text
    assert stolen.status_code in {404, 405}
    assert NAME_B not in stolen.text
    assert data_b.patch.spirit.invite_code not in stolen.text

    captured = logs.getvalue()
    assert "http_access" in captured
    assert hash_user_id(user_a) in captured
    assert hash_user_id(user_b) in captured
    assert str(user_a) not in captured
    assert str(user_b) not in captured
    assert NAME_A not in captured
    assert NAME_B not in captured
    assert CONSENT_A not in captured
    assert CONSENT_B not in captured
    assert data_a.patch.spirit.invite_code not in captured
    assert data_b.patch.spirit.invite_code not in captured
    assert token_a not in captured
    assert token_b not in captured
    assert "eyJ" not in captured
    assert '"error_code": "INVALID_INPUT"' in captured

    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    try:
        async with claimed_transaction(factory, CurrentUser(id=user_b)) as session:
            assert await spirit_repo.fetch_spirit_for_owner(session, user_a) is None
            b_row = await spirit_repo.fetch_spirit_for_owner(session, user_b)
            assert b_row is not None
            assert b_row.id == data_b.resource.id
            others = await session.execute(
                text(
                    "SELECT name, invite_code FROM public.spirits "
                    "WHERE id = :id OR user_id = :owner"
                ),
                {"id": data_a.resource.id, "owner": user_a},
            )
            assert others.first() is None
            consent_count = await session.scalar(
                text("SELECT count(*) FROM public.account_consents WHERE user_id = :id"),
                {"id": user_a},
            )
            assert int(consent_count or 0) == 0
            idem_count = await session.scalar(
                text("SELECT count(*) FROM public.idempotency_records WHERE user_id = :id"),
                {"id": user_a},
            )
            assert int(idem_count or 0) == 0
        async with claimed_transaction(factory, CurrentUser(id=user_a)) as session:
            a_row = await spirit_repo.fetch_spirit_for_owner(session, user_a)
            assert a_row is not None
            assert a_row.id == data_a.resource.id
            assert await spirit_repo.fetch_spirit_for_owner(session, user_b) is None
    finally:
        await engine.dispose()
