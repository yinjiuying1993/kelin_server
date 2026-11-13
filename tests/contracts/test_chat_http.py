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
from app.schemas.chat import ChatTurnResult
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
ROUTER_PATH = Path(__file__).resolve().parents[2] / "app" / "api" / "v1" / "chat.py"


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


def _chat_body(*, client_message_id: UUID | None = None, content: str = "你好") -> dict[str, Any]:
    return {
        "client_message_id": str(client_message_id or uuid4()),
        "content": content,
        "source": "text",
        "context": {
            "timezone": "Asia/Shanghai",
            "local_hour": 21,
            "weather": "cloudy",
            "city": None,
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
    assert "complete_chat_turn" in source
    assert "chat_turn_result_from_settlement" in source
    assert "INTERNAL_ERROR" not in source
    assert "httpx" not in source
    assert "dashscope" not in source.lower()
    assert "integrations.bailian" not in source
    assert "CHAT_COMPLETIONS_URL" not in source


def test_ordinary_chat_replays_after_hatch() -> None:
    url = upgrade_empty_kelin_test_to_head()
    asyncio.run(_assert_chat_http(url))


async def _assert_chat_http(url: str) -> None:
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
    first_body = _chat_body()
    with TestClient(application) as client:
        created = client.post("/api/v1/spirits", json=_spirit_body(), headers=auth)
        missing = client.post(
            "/api/v1/chat",
            json={
                "client_message_id": str(uuid4()),
                "content": "你好",
                "context": first_body["context"],
            },
            headers=auth,
        )
        first = client.post("/api/v1/chat", json=first_body, headers=auth)
        assert first.status_code == 200, first.text
        replay = client.post("/api/v1/chat", json=first_body, headers=auth)
        assert replay.status_code == 200, replay.text
        voice_body = _chat_body()
        voice_body["source"] = "voice"
        voice = client.post("/api/v1/chat", json=voice_body, headers=auth)
        assert voice.status_code == 200, voice.text
    assert created.status_code == 200, created.text
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "INVALID_INPUT"
    assert first.status_code == 200, first.text
    data = ChatTurnResult.model_validate(first.json()["data"])
    replayed = ChatTurnResult.model_validate(replay.json()["data"])
    voiced = ChatTurnResult.model_validate(voice.json()["data"])
    assert data.resource.generation_source == "stub"
    assert data.resource.model_dump().get("onboarding") is None
    assert data.resource.should_extract is False
    assert data.resource.usage.input_units == 0
    assert data.resource.usage.output_units == 0
    assert data.resource.spirit_message.content == "嗯。"
    assert data.resource.speech_audio is None
    assert data.patch.room is not None
    assert data.patch.room.status == "home"
    assert data.patch.model_dump().get("onboarding") is None
    assert data.patch.spirit is not None
    assert data.patch.spirit.hatched_at is not None
    assert replay.status_code == 200
    assert replayed.resource.user_message.id == data.resource.user_message.id
    assert replayed.resource.spirit_message.id == data.resource.spirit_message.id
    assert voiced.resource.speech_audio is None
    dumped = first.text + replay.text + voice.text
    assert token not in dumped
    assert "eyJ" not in dumped
