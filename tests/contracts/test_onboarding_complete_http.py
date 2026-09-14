from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import jwt
from app.core.config import Settings
from app.core.security import CurrentUser, JwtVerifier
from app.db.session import claimed_transaction, create_runtime_engine, create_session_factory
from app.main import create_app
from app.schemas.chat import ChatRequest
from app.schemas.onboarding import OnboardingCompleteResult
from app.schemas.spirit import CreateSpiritRequest
from app.services.chat import settle_chat_turn
from app.services.spirit import create_spirit_if_absent
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
CHAT_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
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


def _spirit_request() -> CreateSpiritRequest:
    return CreateSpiritRequest.model_validate(
        {
            "client_id": str(uuid4()),
            "egg": "warm",
            "name": "未名",
            "consents": {
                "ai_disclosure": {"document_version": "2026-09", "explicitly_accepted": True},
                "data_notice": {"document_version": "2026-09", "displayed": True},
                "user_terms": {"document_version": "2026-09", "displayed": True},
            },
        }
    )


def _chat_request() -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "client_message_id": str(uuid4()),
            "content": "你好",
            "source": "text",
            "onboarding": True,
            "context": {
                "timezone": "Asia/Shanghai",
                "local_hour": 21,
                "weather": "cloudy",
                "city": None,
            },
        }
    )


async def _seed_user(url: str, user_id: UUID) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO auth.users (id) VALUES (:id)"), {"id": user_id})
    await engine.dispose()


async def _seed_rounds(url: str, user_id: UUID, rounds: int) -> int:
    engine = create_runtime_engine(url)
    factory = create_session_factory(engine)
    user = CurrentUser(id=user_id)
    async with claimed_transaction(factory, user) as session:
        created = await create_spirit_if_absent(session, user, _spirit_request())
        version = created.version
        for _ in range(rounds):
            turned = await settle_chat_turn(session, user, _chat_request(), now=CHAT_NOW)
            version = turned.version
    await engine.dispose()
    return version


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
    assert response.status_code == 422
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert payload["data"] is None


def test_step_four_is_incomplete_and_step_five_replays_same_hatched_at() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_http_complete(url))


async def _assert_http_complete(url: str) -> None:
    private, public = _rsa()
    incomplete_sub = uuid4()
    complete_sub = uuid4()
    await _seed_user(url, incomplete_sub)
    await _seed_user(url, complete_sub)
    incomplete_version = await _seed_rounds(url, incomplete_sub, 4)
    complete_version = await _seed_rounds(url, complete_sub, 5)
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
    incomplete_token = _token(private, "kid-a", incomplete_sub)
    complete_token = _token(private, "kid-a", complete_sub)
    complete_body = {
        "client_id": str(uuid4()),
        "expected_spirit_version": complete_version,
    }
    with TestClient(application) as client:
        incomplete = client.post(
            "/api/v1/onboarding/complete",
            json={
                "client_id": str(uuid4()),
                "expected_spirit_version": incomplete_version,
            },
            headers={"Authorization": f"Bearer {incomplete_token}"},
        )
        first = client.post(
            "/api/v1/onboarding/complete",
            json=complete_body,
            headers={"Authorization": f"Bearer {complete_token}"},
        )
        replay = client.post(
            "/api/v1/onboarding/complete",
            json=complete_body,
            headers={"Authorization": f"Bearer {complete_token}"},
        )
    assert incomplete.status_code == 409
    assert incomplete.json()["error"]["code"] == "ONBOARDING_INCOMPLETE"
    assert first.status_code == 200, first.text
    data = OnboardingCompleteResult.model_validate(first.json()["data"])
    replayed = OnboardingCompleteResult.model_validate(replay.json()["data"])
    assert data.patch.spirit is not None
    assert data.patch.spirit.hatched_at is not None
    assert data.patch.onboarding is not None
    assert data.patch.onboarding.required is False
    assert data.patch.onboarding.step == 5
    assert data.patch.room is not None
    assert data.patch.room.weather == "cloudy"
    assert data.patch.room.layers == ["spirit"]
    assert data.patch.spirit.status == "home"
    assert data.patch.spirit.stage == "whelp"
    assert data.patch.snapshot_version == data.patch.spirit.version
    assert data.patch.report is not None
    assert data.patch.report.eligibility.completed_dialogue_rounds == 0
    assert replay.status_code == 200
    assert replayed.patch.spirit is not None
    assert replayed.patch.spirit.hatched_at == data.patch.spirit.hatched_at
    dumped = first.text + replay.text + incomplete.text
    assert incomplete_token not in dumped
    assert complete_token not in dumped
    assert "eyJ" not in dumped
